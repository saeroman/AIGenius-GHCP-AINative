"""Tests for task storage backends."""

import json
from unittest.mock import Mock, patch

import click
import pytest
from azure.data.tables import UpdateMode

import storage


def test_get_storage_uses_local_backend_without_connection_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)

    assert isinstance(storage.get_storage(), storage.LocalStorage)


def test_get_storage_uses_azure_backend_with_connection_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "connection-string")
    service = Mock()

    with patch.object(
        storage.TableServiceClient, "from_connection_string", return_value=service
    ):
        backend = storage.get_storage()

    assert isinstance(backend, storage.AzureTableStorage)
    service.create_table_if_not_exists.assert_called_once_with("tasks")
    service.get_table_client.assert_called_once_with("tasks")


def test_azure_load_decodes_entities_and_sorts_by_id() -> None:
    entities = [
        {
            "PartitionKey": "tasks",
            "RowKey": "2",
            "name": "Second",
            "tags": '["work"]',
            "due_date": "",
            "done": False,
        },
        {
            "PartitionKey": "tasks",
            "RowKey": "1",
            "name": "First",
            "tags": "[]",
            "due_date": "2099-12-31",
            "done": True,
        },
    ]
    table = Mock()
    table.list_entities.return_value = entities
    service = Mock()
    service.get_table_client.return_value = table

    with patch.object(
        storage.TableServiceClient, "from_connection_string", return_value=service
    ):
        tasks = storage.AzureTableStorage("connection-string").load()

    assert [task["id"] for task in tasks] == [1, 2]
    assert tasks[0]["tags"] == []
    assert tasks[1]["tags"] == ["work"]
    assert tasks[1]["due_date"] is None


def test_azure_save_upserts_tasks_and_deletes_stale_entities() -> None:
    table = Mock()
    table.list_entities.return_value = [{"RowKey": "9"}]
    service = Mock()
    service.get_table_client.return_value = table
    task = {
        "id": 1,
        "name": "Test",
        "tags": ["work"],
        "due_date": None,
        "done": False,
    }

    with patch.object(
        storage.TableServiceClient, "from_connection_string", return_value=service
    ):
        storage.AzureTableStorage("connection-string").save([task])

    table.delete_entity.assert_called_once_with(partition_key="tasks", row_key="9")
    table.upsert_entity.assert_called_once_with(
        entity={
            "PartitionKey": "tasks",
            "RowKey": "1",
            "name": "Test",
            "tags": json.dumps(["work"]),
            "due_date": "",
            "done": False,
        },
        mode=UpdateMode.REPLACE,
    )


def test_azure_connection_errors_are_actionable() -> None:
    with patch.object(
        storage.TableServiceClient,
        "from_connection_string",
        side_effect=ValueError("invalid connection string"),
    ):
        with pytest.raises(click.ClickException, match="Could not connect"):
            storage.AzureTableStorage("invalid")
