"""Tests for task storage backends."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import storage


class TestLocalStorage:
    def test_missing_file_returns_empty_list(self, tmp_path: Path) -> None:
        assert storage.LocalStorage(tmp_path / "tasks.json").load() == []

    def test_loads_and_saves_tasks(self, tmp_path: Path) -> None:
        tasks = [{"id": 1, "name": "Test", "tags": ["work"]}]
        task_storage = storage.LocalStorage(tmp_path / "tasks.json")

        task_storage.save(tasks)

        assert task_storage.load() == tasks


class TestAzureTableStorage:
    @pytest.fixture()
    def azure_storage(self) -> tuple[storage.AzureTableStorage, Mock]:
        client = Mock()
        service = Mock()
        service.create_table_if_not_exists.return_value = client
        with patch.object(storage.TableServiceClient, "from_connection_string", return_value=service):
            task_storage = storage.AzureTableStorage("connection-string")
        return task_storage, client

    def test_load_converts_table_entities(
        self, azure_storage: tuple[storage.AzureTableStorage, Mock]
    ) -> None:
        task_storage, client = azure_storage
        client.query_entities.return_value = [
            {
                "PartitionKey": "tasks",
                "RowKey": "7",
                "id": 7,
                "name": "Deploy",
                "tags": '["work", "ops"]',
                "done": False,
            }
        ]

        assert task_storage.load() == [
            {"id": 7, "name": "Deploy", "tags": ["work", "ops"], "done": False}
        ]
        client.query_entities.assert_called_once_with(query_filter="PartitionKey eq 'tasks'")

    def test_save_upserts_tasks_and_deletes_removed_entities(
        self, azure_storage: tuple[storage.AzureTableStorage, Mock]
    ) -> None:
        task_storage, client = azure_storage
        client.query_entities.return_value = [
            {"PartitionKey": "tasks", "RowKey": "1"},
            {"PartitionKey": "tasks", "RowKey": "2"},
        ]
        task = {"id": 1, "name": "Deploy", "tags": ["work"], "done": False}

        task_storage.save([task])

        client.delete_entity.assert_called_once_with(partition_key="tasks", row_key="2")
        entity = client.upsert_entity.call_args.kwargs["entity"]
        assert entity["PartitionKey"] == "tasks"
        assert entity["RowKey"] == "1"
        assert entity["tags"] == json.dumps(["work"])

    def test_connection_failure_raises_storage_error(self) -> None:
        with patch.object(
            storage.TableServiceClient,
            "from_connection_string",
            side_effect=ValueError("invalid connection string"),
        ):
            with pytest.raises(storage.StorageError, match="Could not connect"):
                storage.AzureTableStorage("invalid")


def test_get_storage_uses_azure_when_connection_string_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "connection-string")

    with patch.object(storage, "AzureTableStorage") as azure_storage:
        assert storage.get_storage() is azure_storage.return_value
        azure_storage.assert_called_once_with("connection-string")


def test_get_storage_uses_local_json_when_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    local_path = tmp_path / "tasks.json"

    assert isinstance(storage.get_storage(local_path), storage.LocalStorage)
    assert storage.get_storage(local_path).path == local_path
