"""Storage backends for task persistence."""

import json
import os
from pathlib import Path
from typing import Protocol

from azure.data.tables import TableServiceClient
from dotenv import load_dotenv


TABLE_NAME = "tasks"
PARTITION_KEY = "tasks"


class StorageError(Exception):
    """Raised when task storage cannot be accessed."""


class TaskStorage(Protocol):
    """Interface implemented by task storage backends."""

    def load(self) -> list[dict]:
        """Load all stored tasks."""

    def save(self, tasks: list[dict]) -> None:
        """Replace the stored tasks."""


class LocalStorage:
    """Persist tasks in a local JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            if not isinstance(data, list):
                raise ValueError("tasks file must contain a JSON array")
            return data
        except (json.JSONDecodeError, OSError, ValueError):
            return []

    def save(self, tasks: list[dict]) -> None:
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(tasks, file, indent=2)


class AzureTableStorage:
    """Persist tasks in an Azure Table Storage table."""

    def __init__(self, connection_string: str) -> None:
        try:
            self.service_client = TableServiceClient.from_connection_string(connection_string)
            self.service_client.create_table_if_not_exists(TABLE_NAME)
            self.table_client = self.service_client.get_table_client(TABLE_NAME)
        except Exception as error:
            raise StorageError("Could not connect to Azure Table Storage.") from error

    def load(self) -> list[dict]:
        try:
            tasks = []
            for entity in self.table_client.query_entities(
                f"PartitionKey eq '{PARTITION_KEY}'"
            ):
                task = {
                    key: value
                    for key, value in entity.items()
                    if key not in {"PartitionKey", "RowKey", "Timestamp", "etag"}
                }
                task["id"] = int(entity["RowKey"])
                task["tags"] = json.loads(task.get("tags", "[]"))
                task.setdefault("due_date", None)
                tasks.append(task)
            return tasks
        except Exception as error:
            raise StorageError("Could not load tasks from Azure Table Storage.") from error

    def save(self, tasks: list[dict]) -> None:
        try:
            task_ids = {str(task["id"]) for task in tasks}
            existing_entities = list(
                self.table_client.query_entities(f"PartitionKey eq '{PARTITION_KEY}'")
            )
            for task in tasks:
                entity = {
                    "PartitionKey": PARTITION_KEY,
                    "RowKey": str(task["id"]),
                }
                for key, value in task.items():
                    if key == "id" or value is None:
                        continue
                    entity[key] = json.dumps(value) if key == "tags" else value
                self.table_client.upsert_entity(entity=entity)
            for entity in existing_entities:
                if entity["RowKey"] not in task_ids:
                    self.table_client.delete_entity(
                        partition_key=PARTITION_KEY, row_key=entity["RowKey"]
                    )
        except Exception as error:
            raise StorageError("Could not save tasks to Azure Table Storage.") from error


def get_storage(local_path: Path) -> TaskStorage:
    """Return Azure storage when configured, otherwise local JSON storage."""
    load_dotenv()
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if connection_string:
        return AzureTableStorage(connection_string)
    return LocalStorage(local_path)
