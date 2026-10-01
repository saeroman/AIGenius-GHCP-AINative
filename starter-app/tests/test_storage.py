"""Tests for task storage backends."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import storage


def test_get_storage_defaults_to_local_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.setattr(storage, "load_dotenv", lambda: None)

    result = storage.get_storage(tmp_path / "tasks.json")

    assert isinstance(result, storage.LocalStorage)


def test_get_storage_uses_azure_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "mock-connection")
    monkeypatch.setattr(storage, "load_dotenv", lambda: None)
    with patch.object(storage.TableServiceClient, "from_connection_string") as create_client:
        result = storage.get_storage(tmp_path / "tasks.json")

    assert isinstance(result, storage.AzureTableStorage)
    create_client.assert_called_once_with("mock-connection")
    result.service_client.create_table_if_not_exists.assert_called_once_with("tasks")


def test_azure_storage_loads_task_entities() -> None:
    table_client = MagicMock()
    table_client.query_entities.return_value = [
        {
            "PartitionKey": "tasks",
            "RowKey": "1",
            "name": "Test",
            "tags": '["work"]',
            "done": False,
        }
    ]
    service_client = MagicMock()
    service_client.get_table_client.return_value = table_client

    with patch.object(
        storage.TableServiceClient, "from_connection_string", return_value=service_client
    ):
        result = storage.AzureTableStorage("mock-connection").load()

    assert result == [
        {"name": "Test", "tags": ["work"], "done": False, "id": 1, "due_date": None}
    ]
    service_client.create_table_if_not_exists.assert_called_once_with("tasks")


def test_azure_storage_replaces_entities_and_serializes_tags() -> None:
    table_client = MagicMock()
    table_client.query_entities.return_value = [
        {"PartitionKey": "tasks", "RowKey": "1"},
        {"PartitionKey": "tasks", "RowKey": "2"},
    ]
    service_client = MagicMock()
    service_client.get_table_client.return_value = table_client
    task = {"id": 1, "name": "Test", "tags": ["work"], "due_date": None}

    with patch.object(
        storage.TableServiceClient, "from_connection_string", return_value=service_client
    ):
        storage.AzureTableStorage("mock-connection").save([task])

    table_client.upsert_entity.assert_called_once_with(
        entity={
            "PartitionKey": "tasks",
            "RowKey": "1",
            "name": "Test",
            "tags": '["work"]',
        }
    )
    table_client.delete_entity.assert_called_once_with(partition_key="tasks", row_key="2")


def test_azure_connection_errors_are_wrapped() -> None:
    with (
        patch.object(
            storage.TableServiceClient,
            "from_connection_string",
            side_effect=ValueError("invalid connection"),
        ),
        pytest.raises(storage.StorageError, match="Could not connect"),
    ):
        storage.AzureTableStorage("invalid")
