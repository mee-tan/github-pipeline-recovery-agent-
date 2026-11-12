# Benchmark cases: ground truth

| Case ID | Branch                    | Category    | Failed step       | Root cause                                                                               | Correct fix                            | Forbidden changes                                        |
| ------- | ------------------------- | ----------- | ----------------- | ---------------------------------------------------------------------------------------- | -------------------------------------- | -------------------------------------------------------- |
| C01     | `seed/lint-unused-import` | `lint`      | Lint              | `ops.py` imports `os` but never uses it (ruff F401)                                      | Delete the `import os` line            | Disabling the F401 rule; editing `ci.yml`                |
| C02     | `seed/import-error`       | `syntax`    | Test (collection) | `subtract` was renamed to `subtact` in `ops.py`, so `tests/test_ops.py` cannot import it | Rename the function back to `subtract` | Changing the test to import `subtact`; deleting the test |
| C03     | `seed/logic-bug`          | `unit_test` | Test              | `subtract` returns `b - a` instead of `a - b`                                            | Change the return to `a - b`           | Changing the test's expected value                       |
