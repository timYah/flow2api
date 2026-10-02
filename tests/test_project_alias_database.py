import tempfile
import unittest

from src.core.database import Database
from src.core.models import Project, Token


class ProjectAliasDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(db_path=f"{self._temp_dir.name}/flow.db")
        await self.db.init_db()
        self.token_id = await self.db.add_token(
            Token(
                st="st-project-test",
                at="at-project-test",
                email="project@example.com",
            )
        )

    async def asyncTearDown(self):
        self._temp_dir.cleanup()

    async def test_project_alias_is_persisted_and_queryable(self):
        project = Project(
            project_id="real-project-id",
            client_project_id="client-project-id",
            token_id=self.token_id,
            project_name="client-project-id",
        )
        await self.db.add_project(project)

        by_real_id = await self.db.get_project_by_id("real-project-id")
        by_client_id = await self.db.get_project_by_client_id("client-project-id")

        self.assertEqual(by_real_id.client_project_id, "client-project-id")
        self.assertEqual(by_client_id.project_id, "real-project-id")
        self.assertEqual(by_client_id.token_id, self.token_id)

    async def test_client_alias_is_globally_unique(self):
        first = Project(
            project_id="real-project-1",
            client_project_id="same-client-id",
            token_id=self.token_id,
            project_name="same-client-id",
        )
        second = Project(
            project_id="real-project-2",
            client_project_id="same-client-id",
            token_id=self.token_id,
            project_name="same-client-id",
        )
        await self.db.add_project(first)

        with self.assertRaises(Exception):
            await self.db.add_project(second)

    async def test_client_alias_can_be_cleared_for_rebinding(self):
        project = Project(
            project_id="real-project-id",
            client_project_id="client-project-id",
            token_id=self.token_id,
            project_name="client-project-id",
            is_active=False,
        )
        await self.db.add_project(project)
        await self.db.clear_project_client_id("real-project-id")

        self.assertIsNone(await self.db.get_project_by_client_id("client-project-id"))

    async def test_existing_projects_table_is_migrated_without_losing_projects(self):
        async with self.db._connect(write=True) as conn:
            await conn.execute("ALTER TABLE projects RENAME TO projects_with_alias")
            await conn.execute(
                """
                CREATE TABLE projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT UNIQUE NOT NULL,
                    token_id INTEGER NOT NULL,
                    project_name TEXT NOT NULL,
                    tool_name TEXT DEFAULT 'PINHOLE',
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES tokens(id)
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO projects (
                    id, project_id, token_id, project_name, tool_name, is_active, created_at
                )
                SELECT id, project_id, token_id, project_name, tool_name, is_active, created_at
                FROM projects_with_alias
                """
            )
            await conn.execute(
                """
                INSERT INTO projects (project_id, token_id, project_name)
                VALUES ('legacy-project-id', ?, 'Legacy project')
                """,
                (self.token_id,),
            )
            await conn.execute("DROP TABLE projects_with_alias")
            await conn.commit()

        # main.py calls init_db before check_and_migrate_db. The first call
        # must also work while this legacy schema is still missing the column.
        await self.db.init_db()
        await self.db.check_and_migrate_db()

        migrated = await self.db.get_project_by_id("legacy-project-id")
        self.assertIsNotNone(migrated)
        self.assertIsNone(migrated.client_project_id)
        async with self.db._connect() as conn:
            columns = await conn.execute("PRAGMA table_info(projects)")
            column_names = {row[1] for row in await columns.fetchall()}
        self.assertIn("client_project_id", column_names)


if __name__ == "__main__":
    unittest.main()
