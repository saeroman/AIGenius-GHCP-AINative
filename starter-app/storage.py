"""Task persistence backends for the Task Manager CLI."""

import json
import os
from pathlib import Path
from typing import Protocol

import click
from azure.core.exceptions import AzureError
from azure.data.tables import TableServiceClient, UpdateMode
from dotenv import load_dotenv

TASKS_FILE = Path(__file__).resolve().with_name("tasks.json")
TABLE_NAME = "tasks"
PARTITION_KEY = "tasks"

load_dotenv()


class TaskStorage(Protocol):
    """Interface implemented by task storage backends."""

    def load(self) -> list[dict]:
        """Load all stored tasks."""
        ...

    def save(self, tasks: list[dict]) -> None:
        """Replace the stored tasks."""
        ...


class LocalStorage:
    """Store tasks in the local JSON file."""

    def load(self) -> list[dict]:
        """Load tasks from the JSON file, returning an empty list if unavailable."""
        if not TASKS_FILE.exists():
            return []
        try:
            with TASKS_FILE.open("r", encoding="utf-8") as tasks_file:
                data = json.load(tasks_file)
            if not isinstance(data, list):
                raise ValueError("tasks file must contain a JSON array")
            return data
        except (json.JSONDecodeError, OSError, ValueError):
            click.echo("Warning: Could not read tasks file. Starting fresh.", err=True)
            return []

    def save(self, tasks: list[dict]) -> None:
        """Save tasks to the JSON file."""
        with TASKS_FILE.open("w", encoding="utf-8") as tasks_file:
            json.dump(tasks, tasks_file, indent=2)


class AzureTableStorage:
    """Store tasks as entities in Azure Table Storage."""

    def __init__(self, connection_string: str) -> None:
        try:
            service = TableServiceClient.from_connection_string(connection_string)
            service.create_table_if_not_exists(TABLE_NAME)
            self.table_client = service.get_table_client(TABLE_NAME)
        except (AzureError, ValueError) as error:
            raise click.ClickException(
                f"Could not connect to Azure Table Storage: {error}"
            ) from error

    def load(self) -> list[dict]:
        """Load and decode all task entities."""
        try:
            tasks = []
            for entity in self.table_client.list_entities():
                task = {
                    key: value
                    for key, value in entity.items()
                    if key not in {"PartitionKey", "RowKey", "Timestamp", "etag"}
                }
                task["id"] = int(entity["RowKey"])
                if "tags" in task:
                    task["tags"] = json.loads(task["tags"])
                if task.get("due_date") == "":
                    task["due_date"] = None
                tasks.append(task)
            return sorted(tasks, key=lambda task: task["id"])
        except (AzureError, json.JSONDecodeError, KeyError, ValueError) as error:
            raise click.ClickException(
                f"Could not load tasks from Azure Table Storage: {error}"
            ) from error

    def save(self, tasks: list[dict]) -> None:
        """Replace all task entities with the supplied task list."""
        try:
            task_ids = {str(task["id"]) for task in tasks}
            for entity in self.table_client.list_entities():
                if entity["RowKey"] not in task_ids:
                    self.table_client.delete_entity(
                        partition_key=PARTITION_KEY,
                        row_key=entity["RowKey"],
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
                if task.get("due_date") is None:
                    entity["due_date"] = ""
                self.table_client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)
        except AzureError as error:
            raise click.ClickException(
                f"Could not save tasks to Azure Table Storage: {error}"
            ) from error


def get_storage() -> TaskStorage:
    """Select Azure storage when configured, or use the local JSON file."""
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if connection_string:
        return AzureTableStorage(connection_string)
    return LocalStorage()
