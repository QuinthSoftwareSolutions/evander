"""Opt-in SQL Server qualification; uses only generated disposable databases."""

import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import unittest

from evander_browser_sessions_addon.worker import Supervisor, WorkerSettings


class DatabaseSupervisor(Supervisor):
    # This qualification exercises SQL, without requiring unrelated Docker resources.
    def inspect(self, name, kind="container"):
        return None

    def remove_firewall(self, lease):
        pass


@unittest.skipUnless(
    os.environ.get("EVANDER_BROWSER_TEST_SQL_SERVER")
    and os.environ.get("EVANDER_BROWSER_TEST_SQL_PASSWORD"),
    "Configure a dedicated SQL Server for disposable database qualification",
)
class PreviewDatabaseTests(unittest.TestCase):
    def test_fresh_databases_are_scoped_retryable_and_removed_on_close(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = WorkerSettings(
                worker_id="worker-test",
                worker_root=Path(folder) / "worker",
                runtime_dir=Path(folder) / "runtime",
                sql_server=os.environ["EVANDER_BROWSER_TEST_SQL_SERVER"],
                sql_admin_password_env="EVANDER_BROWSER_TEST_SQL_PASSWORD",
                sqlcmd=os.environ.get(
                    "EVANDER_BROWSER_TEST_SQLCMD", "/opt/mssql-tools18/bin/sqlcmd"
                ),
            )
            supervisor = DatabaseSupervisor(settings)
            leases = []

            def query(user, password, database, statement):
                return subprocess.run(
                    [settings.sqlcmd, "-S", settings.sql_server, "-U", user,
                     "-d", database, "-C", "-b", "-l", "10", "-W", "-h", "-1",
                     "-s", "|", "-Q", "SET NOCOUNT ON; " + statement],
                    env={**os.environ, "SQLCMDPASSWORD": password},
                    capture_output=True, text=True, timeout=30,
                )

            try:
                for _ in range(2):
                    token = secrets.token_hex(12)
                    lease = {"id": "browser-" + token, "name": "evbs-" + token,
                             "state": "accepted", "error": None}
                    leases.append(lease)
                    connection = supervisor.provision_database(lease)
                    # Recovered/retried provisioning preserves the existing database and login.
                    self.assertEqual(
                        supervisor.provision_database(supervisor.get(lease["id"])), connection
                    )
                    result = query(
                        lease["database_name"], lease["database_password"],
                        lease["database_name"],
                        "CREATE TABLE dbo.Qualification (id int NOT NULL); "
                        "INSERT INTO dbo.Qualification VALUES (1); "
                        "SELECT COUNT(*) FROM dbo.Qualification;",
                    )
                    self.assertEqual(result.returncode, 0, "Own-database DDL failed")
                    self.assertEqual(result.stdout.strip(), "1")

                for lease, other in (leases, list(reversed(leases))):
                    result = query(
                        lease["database_name"], lease["database_password"],
                        lease["database_name"],
                        "SELECT IS_SRVROLEMEMBER('sysadmin'), "
                        "(SELECT COUNT(*) FROM sys.fn_my_permissions(NULL,'SERVER') "
                        "WHERE permission_name IN ('CONTROL SERVER','ALTER ANY LOGIN')), "
                        f"HAS_DBACCESS('{other['database_name']}');",
                    )
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.stdout.strip(), "0|0|0")
                    result = query(
                        lease["database_name"], lease["database_password"],
                        lease["database_name"],
                        f"SELECT COUNT(*) FROM [{other['database_name']}].sys.tables;",
                    )
                    self.assertNotEqual(result.returncode, 0, "Cross-preview access allowed")
            finally:
                for lease in leases:
                    self.assertEqual(supervisor.close(lease["id"]), {"released": True})

            for lease in leases:
                name = lease["database_name"]
                result = query(
                    settings.sql_admin_user,
                    os.environ[settings.sql_admin_password_env], "master",
                    f"SELECT CASE WHEN DB_ID('{name}') IS NULL THEN 1 ELSE 0 END, "
                    f"CASE WHEN SUSER_ID('{name}') IS NULL THEN 1 ELSE 0 END;",
                )
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout.strip(), "1|1")
                stored = supervisor.get(lease["id"])
                self.assertEqual(stored["state"], "closed")
                self.assertNotIn("database_password", stored)
                self.assertEqual(supervisor.close(lease["id"]), {"released": True})


if __name__ == "__main__":
    unittest.main()
