"""
Tests for compound modules with multiple processors referencing .mkape files.

This tests the case where a module has multiple processors in its Processors list,
and those processors reference other .mkape files in the Executable field instead
of binary executables.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from kape_modules import run_module


class TestCompoundProcessors(unittest.TestCase):
    """Test compound processors that reference .mkape files in Executable field."""

    def test_processor_with_mkape_executable(self):
        """Test that a processor with .mkape in Executable field is handled as sub-module."""
        # Create a temporary modules directory
        modules_dir = Path("/tmp/test_compound_processors")
        modules_dir.mkdir(exist_ok=True)

        # Create a sub-module
        sub_module_data = {
            "Description": "Sub-module",
            "Category": "Test",
            "Id": "sub-module-id-001",
            "Processors": [
                {
                    "Executable": "echo",
                    "CommandLine": "SubModule executed",
                }
            ],
        }

        # Create compound module with processor referencing .mkape file
        compound_module_data = {
            "Description": "Compound Processors Module",
            "Category": "Test",
            "Id": "compound-module-id-001",
            "Processors": [
                {
                    "Executable": "SubModule.mkape",
                    "CommandLine": "",
                }
            ],
        }

        # Mock subprocess.run to track execution
        with patch("kape_modules.subprocess.run") as mock_run, \
             patch("kape_modules.find_module_files") as mock_find, \
             patch("kape_modules.load_module") as mock_load:

            # Setup mocks
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "SubModule executed\n"
            mock_run.return_value.stderr = ""

            sub_file = modules_dir / "SubModule.mkape"
            mock_find.return_value = [sub_file]
            mock_load.return_value = sub_module_data

            # Run the compound module
            processed_ids = set()
            run_module(
                module_name="CompoundModule",
                module_data=compound_module_data,
                module_file=modules_dir / "CompoundModule.mkape",
                modules_dir=modules_dir,
                msource="/tmp",
                mdest="/tmp/mdest",
                mef=None,
                mvars={},
                debug=False,
                processed_ids=processed_ids,
                dry_run=False,
            )

            # Verify that find_module_files was called with the sub-module name
            mock_find.assert_called_once_with(modules_dir, "SubModule")

            # Verify that the sub-module was loaded
            mock_load.assert_called_once_with(sub_file)

            # Verify that subprocess.run was called (for the echo command in sub-module)
            self.assertTrue(mock_run.called)
            call_args = mock_run.call_args[0][0]
            self.assertIn("echo", call_args)

    def test_multiple_mkape_processors(self):
        """Test a module with multiple processors all referencing .mkape files."""
        modules_dir = Path("/tmp/test_compound_processors_multi")
        modules_dir.mkdir(exist_ok=True)

        # Create two sub-modules
        sub_module1_data = {
            "Description": "Sub-module 1",
            "Category": "Test",
            "Id": "sub-module-1-id-001",
            "Processors": [
                {
                    "Executable": "echo",
                    "CommandLine": "SubModule1 executed",
                }
            ],
        }

        sub_module2_data = {
            "Description": "Sub-module 2",
            "Category": "Test",
            "Id": "sub-module-2-id-001",
            "Processors": [
                {
                    "Executable": "echo",
                    "CommandLine": "SubModule2 executed",
                }
            ],
        }

        # Create compound module with multiple processors referencing .mkape files
        compound_module_data = {
            "Description": "Multi Compound Processors Module",
            "Category": "Test",
            "Id": "compound-module-id-002",
            "Processors": [
                {
                    "Executable": "SubModule1.mkape",
                    "CommandLine": "",
                },
                {
                    "Executable": "SubModule2.mkape",
                    "CommandLine": "",
                },
            ],
        }

        # Mock subprocess.run to track execution
        with patch("kape_modules.subprocess.run") as mock_run, \
             patch("kape_modules.find_module_files") as mock_find, \
             patch("kape_modules.load_module") as mock_load:

            # Setup mocks
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Executed\n"
            mock_run.return_value.stderr = ""

            # Mock find_module_files to return appropriate files
            def find_side_effect(modules_dir, name):
                return [modules_dir / f"{name}.mkape"]

            mock_find.side_effect = find_side_effect

            # Mock load_module to return appropriate data
            def load_side_effect(file_path):
                if "SubModule1" in str(file_path):
                    return sub_module1_data
                elif "SubModule2" in str(file_path):
                    return sub_module2_data
                return {}

            mock_load.side_effect = load_side_effect

            # Run the compound module
            processed_ids = set()
            run_module(
                module_name="MultiCompoundModule",
                module_data=compound_module_data,
                module_file=modules_dir / "MultiCompoundModule.mkape",
                modules_dir=modules_dir,
                msource="/tmp",
                mdest="/tmp/mdest",
                mef=None,
                mvars={},
                debug=False,
                processed_ids=processed_ids,
                dry_run=False,
            )

            # Verify that both sub-modules were found
            self.assertEqual(mock_find.call_count, 2)

            # Verify that both sub-modules were loaded
            self.assertEqual(mock_load.call_count, 2)

            # Verify that subprocess.run was called twice (once for each sub-module)
            self.assertEqual(mock_run.call_count, 2)

    def test_mkape_processor_not_found(self):
        """Test that missing .mkape file in processor is handled gracefully."""
        modules_dir = Path("/tmp/test_compound_processors_missing")
        modules_dir.mkdir(exist_ok=True)

        # Create compound module with processor referencing non-existent .mkape file
        compound_module_data = {
            "Description": "Compound Module with Missing Sub-Module",
            "Category": "Test",
            "Id": "compound-module-id-003",
            "Processors": [
                {
                    "Executable": "NonExistent.mkape",
                    "CommandLine": "",
                }
            ],
        }

        # Mock find_module_files to return empty list
        with patch("kape_modules.find_module_files") as mock_find, \
             patch("kape_modules.logging") as mock_logging:

            mock_find.return_value = []

            # Run the compound module
            processed_ids = set()
            run_module(
                module_name="CompoundModule",
                module_data=compound_module_data,
                module_file=modules_dir / "CompoundModule.mkape",
                modules_dir=modules_dir,
                msource="/tmp",
                mdest="/tmp/mdest",
                mef=None,
                mvars={},
                debug=False,
                processed_ids=processed_ids,
                dry_run=False,
            )

            # Verify that a warning was logged
            mock_logging.warning.assert_called()
            warning_call_args = str(mock_logging.warning.call_args)
            self.assertIn("NonExistent", warning_call_args)

    def test_dry_run_with_mkape_processors(self):
        """Test that dry run works correctly with .mkape processors."""
        modules_dir = Path("/tmp/test_compound_processors_dryrun")
        modules_dir.mkdir(exist_ok=True)

        # Create a sub-module
        sub_module_data = {
            "Description": "Sub-module",
            "Category": "Test",
            "Id": "sub-module-id-004",
            "Processors": [
                {
                    "Executable": "echo",
                    "CommandLine": "SubModule executed",
                }
            ],
        }

        # Create compound module
        compound_module_data = {
            "Description": "Compound Module for Dry Run",
            "Category": "Test",
            "Id": "compound-module-id-004",
            "Processors": [
                {
                    "Executable": "SubModule.mkape",
                    "CommandLine": "",
                }
            ],
        }

        # Mock subprocess.run to ensure it's NOT called in dry run
        with patch("kape_modules.subprocess.run") as mock_run, \
             patch("kape_modules.find_module_files") as mock_find, \
             patch("kape_modules.load_module") as mock_load:

            # Setup mocks
            sub_file = modules_dir / "SubModule.mkape"
            mock_find.return_value = [sub_file]
            mock_load.return_value = sub_module_data

            # Run the compound module in dry run mode
            processed_ids = set()
            run_module(
                module_name="CompoundModule",
                module_data=compound_module_data,
                module_file=modules_dir / "CompoundModule.mkape",
                modules_dir=modules_dir,
                msource="/tmp",
                mdest="/tmp/mdest",
                mef=None,
                mvars={},
                debug=False,
                processed_ids=processed_ids,
                dry_run=True,  # Enable dry run
            )

            # Verify that subprocess.run was NOT called
            mock_run.assert_not_called()

    def test_mixed_processors_mkape_and_executable(self):
        """Test that only .mkape processors are treated as sub-modules, not regular ones."""
        modules_dir = Path("/tmp/test_compound_processors_mixed")
        modules_dir.mkdir(exist_ok=True)

        # Create a module with both .mkape and regular executables
        # Only the .mkape should be processed as sub-module
        mixed_module_data = {
            "Description": "Mixed Processors Module",
            "Category": "Test",
            "Id": "mixed-module-id-001",
            "Processors": [
                {
                    "Executable": "SubModule.mkape",  # This should be sub-module
                    "CommandLine": "",
                },
                {
                    "Executable": "echo",  # This should NOT be treated as sub-module
                    "CommandLine": "Regular command",
                },
            ],
        }

        sub_module_data = {
            "Description": "Sub-module",
            "Category": "Test",
            "Id": "sub-module-id-005",
            "Processors": [
                {
                    "Executable": "echo",
                    "CommandLine": "SubModule executed",
                }
            ],
        }

        with patch("kape_modules.subprocess.run") as mock_run, \
             patch("kape_modules.find_module_files") as mock_find, \
             patch("kape_modules.load_module") as mock_load:

            # Setup mocks
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Executed\n"
            mock_run.return_value.stderr = ""

            sub_file = modules_dir / "SubModule.mkape"
            mock_find.return_value = [sub_file]
            mock_load.return_value = sub_module_data

            # Run the mixed module
            processed_ids = set()
            run_module(
                module_name="MixedModule",
                module_data=mixed_module_data,
                module_file=modules_dir / "MixedModule.mkape",
                modules_dir=modules_dir,
                msource="/tmp",
                mdest="/tmp/mdest",
                mef=None,
                mvars={},
                debug=False,
                processed_ids=processed_ids,
                dry_run=False,
            )

            # Verify that find_module_files was called for the .mkape processor
            mock_find.assert_called_once_with(modules_dir, "SubModule")

            # Verify that the sub-module was loaded and executed
            mock_load.assert_called_once()

            # The subprocess should be called once (for the echo in sub-module)
            # The second processor (regular echo) should NOT be executed because
            # when .mkape processors are present, we only process those and return
            self.assertEqual(mock_run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
