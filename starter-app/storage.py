"""Task storage backends."""

import json
import os
from pathlib import Path
from typing import Protocol

from azure.core.exceptions import AzureError
from azure.data.tables import TableServiceClient, UpdateMode
from dotenv import load_dotenv


class TaskStorage(Protocol):
    """Interface implemented by task storage backends."""

    def load(self) -> list[dict]:
        """Return all stored tasks."""
        ...

    def save(self, tasks: list[dict]) -> None:
        """Replace all stored tasks."""
        ...


class StorageError(Exception):
    """Raised when a storage backend cannot complete an operation."""


class LocalStorage:
    """Persist tasks in a local JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[dict]:
        """Return tasks from the JSON file, or an empty list if unavailable."""
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError, ValueError):
            pass
        return []

    def save(self, tasks: list[dict]) -> None:
        """Write tasks to the JSON file."""
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(tasks, file, indent=2)


class AzureTableStorage:
    """Persist tasks in Azure Table Storage."""

    partition_key = "tasks"

    def __init__(self, connection_string: str, table_name: str = "tasks") -> None:
        try:
            service = TableServiceClient.from_connection_string(connection_string)
            self.table_client = service.create_table_if_not_exists(table_name=table_name)
        except (AzureError, ValueError) as error:
            raise StorageError("Could not connect to Azure Table Storage.") from error

    def load(self) -> list[dict]:
        """Return tasks from the Azure table."""
        try:
            entities = self.table_client.query_entities(
                query_filter=f"PartitionKey eq '{self.partition_key}'"
            )
            tasks = []
            for entity in entities:
                task = {
                    key: value
                    for key, value in entity.items()
                    if key not in {"PartitionKey", "RowKey", "Timestamp", "etag"}
                }
                task["id"] = int(entity["RowKey"])
                task["tags"] = json.loads(task.get("tags", "[]"))
                tasks.append(task)
            return tasks
        except (AzureError, KeyError, TypeError, ValueError) as error:
            raise StorageError("Could not read tasks from Azure Table Storage.") from error

    def save(self, tasks: list[dict]) -> None:
        """Replace all tasks in the Azure table."""
        try:
            current_entities = list(
                self.table_client.query_entities(
                    query_filter=f"PartitionKey eq '{self.partition_key}'"
                )
            )
            task_ids = {str(task["id"]) for task in tasks}
            for entity in current_entities:
                if entity["RowKey"] not in task_ids:
                    self.table_client.delete_entity(
                        partition_key=self.partition_key,
                        row_key=entity["RowKey"],
                    )

            for task in tasks:
                entity = {
                    **task,
                    "PartitionKey": self.partition_key,
                    "RowKey": str(task["id"]),
                    "tags": json.dumps(task.get("tags", [])),
                }
                self.table_client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)
        except (AzureError, KeyError, TypeError, ValueError) as error:
            raise StorageError("Could not save tasks to Azure Table Storage.") from error


def get_storage(local_path: Path | None = None) -> TaskStorage:
    """Return Azure storage when configured, or local JSON storage otherwise."""
    load_dotenv()
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if connection_string:
        return AzureTableStorage(connection_string)
    path = local_path or Path(__file__).resolve().with_name("tasks.json")
    return LocalStorage(path)
