import concurrent.futures
import os
from threading import Thread
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from psycopg2.extras import Json

from shared.di import injector
from shared.di.dependency_provider import service
from shared.postgresql_backend import ConnectionHandler, setup_di as postgres_setup_di
from shared.postgresql_backend.connection_handler import DatabaseError
from shared.postgresql_backend.pg_connection_handler import (
    ConnectionContext,
    PgConnectionHandlerService,
)
from shared.services import EnvironmentService, setup_di as util_setup_di
from shared.tests import reset_di  # noqa
from shared.util import BadCodingError


@pytest.fixture(autouse=True)
def provide_di(reset_di):  # noqa
    util_setup_di()
    postgres_setup_di()
    yield


@pytest.fixture()
def handler(provide_di):
    yield injector.get(ConnectionHandler)


def test_connection_context(handler):
    connection = MagicMock()
    handler.get_connection = gc = MagicMock(return_value=connection)
    handler.put_connection = pc = MagicMock()

    context = ConnectionContext(handler)
    assert context.connection_handler == handler
    gc.assert_not_called()

    with context:
        connection.__enter__.assert_called()
        gc.assert_called()
    connection.__exit__.assert_called()
    pc.assert_called_with(connection)


def test_init_error():
    connect = MagicMock()
    connect.side_effect = psycopg2.Error
    with patch("psycopg2.connect", new=connect):
        with pytest.raises(DatabaseError):
            PgConnectionHandlerService()


def test_get_connection(handler):
    connection = MagicMock()
    handler._semaphore = semaphore = MagicMock()
    handler.connection_pool = pool = MagicMock()

    pool.getconn = gc = MagicMock(return_value=connection)

    assert handler.get_connection() == connection
    semaphore.acquire.assert_called()
    gc.assert_called()
    assert connection.autocommit is False


def test_get_connection_error(handler):
    handler.get_connection()
    with pytest.raises(BadCodingError):
        handler.get_connection()


def test_get_connection_lock(handler):
    conn = handler.get_connection()
    thread = Thread(target=handler.get_connection)
    thread.start()
    thread.join(0.5)
    assert thread.is_alive()
    handler.put_connection(conn)
    thread.join(0.05)
    assert not thread.is_alive()


def test_get_connection_different():
    os.environ["DATASTORE_MAX_CONNECTIONS"] = "2"
    injector.get(EnvironmentService).cache = {}
    handler = service(PgConnectionHandlerService)()

    def get_connection_from_thread():
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(handler.get_connection)
            return future.result()

    connection1 = get_connection_from_thread()
    connection2 = get_connection_from_thread()
    assert connection1 != connection2


def test_put_connection(handler):
    connection = MagicMock()
    handler.get_current_connection = gcc = MagicMock(return_value=connection)
    handler.set_current_connection = scc = MagicMock()
    handler._semaphore = semaphore = MagicMock()
    handler.connection_pool = pool = MagicMock()

    pool.putconn = pc = MagicMock()

    handler.put_connection(connection)
    pc.assert_called_with(connection)
    semaphore.release.assert_called()
    gcc.assert_called()
    scc.assert_called_with(None)


def test_put_connection_invalid_connection(handler):
    handler._storage = MagicMock()
    handler._storage.connection = MagicMock()

    with pytest.raises(BadCodingError):
        handler.put_connection(MagicMock())


def test_get_connection_context(handler):
    with patch(
        "shared.postgresql_backend.pg_connection_handler.ConnectionContext"
    ) as context:
        handler.get_connection_context()
        context.assert_called_with(handler)


def test_to_json(handler):
    json = handler.to_json({"a": "a", "b": "b"})
    assert type(json) is Json
    assert str(json) == '\'{"a": "a", "b": "b"}\''


def setup_mocked_connection(handler):
    cursor = MagicMock(name="cursor")
    cursor.execute = MagicMock(name="execute")
    cursor_context = MagicMock(name="cursor_context")
    cursor_context.__enter__ = MagicMock(return_value=cursor, name="enter")
    mock = MagicMock(name="connection_mock")
    mock.cursor = MagicMock(return_value=cursor_context, name="cursor_func")
    handler.get_current_connection = MagicMock(return_value=mock)
    return cursor


def test_execute(handler):
    cursor = setup_mocked_connection(handler)

    handler.execute("", "")
    cursor.execute.assert_called()


def test_query(handler):
    cursor = setup_mocked_connection(handler)
    result = MagicMock()
    cursor.fetchall = MagicMock(return_value=result)

    assert handler.query("", "") == result
    cursor.execute.assert_called()
    cursor.fetchall.assert_called()


def test_query_single_value(handler):
    cursor = setup_mocked_connection(handler)
    result = MagicMock()
    result[0] = MagicMock()
    cursor.fetchone = MagicMock(return_value=result)

    assert handler.query_single_value("", "") == result[0]
    cursor.execute.assert_called()
    cursor.fetchone.assert_called()


def test_query_single_value_none(handler):
    cursor = setup_mocked_connection(handler)
    cursor.fetchone = MagicMock(return_value=None)

    assert handler.query_single_value("", "") is None


def test_query_list_of_single_values(handler):
    handler.query = MagicMock()
    handler.query_list_of_single_values("", "")
    handler.query.assert_called_with("", "", [])


def test_shutdown(handler):
    handler.connection_pool = pool = MagicMock()

    handler.shutdown()
    pool.closeall.assert_called()
