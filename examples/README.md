# Voyager Examples

## Directory structure

```
examples/
  _sources/          <- Gold master copies (never modify these)
    shop-dto/        <- Source files for the shop-dto project
  shop-dto/          <- Runtime copy (safe to modify, will be reset)
  reset.py           <- Script to reset all examples from _sources/
```

- **`_sources/`** contains the pristine, read-only source files. Never edit files here during testing.
- The runtime directories (e.g. `shop-dto/`) are working copies that Voyager operates on. After each test run, reset them.

## How to reset

```bash
# Reset a specific project
python examples/reset.py shop-dto

# Reset all projects
python examples/reset.py
```

This deletes all files in the runtime directory and copies fresh files from `_sources/`.

## shop-dto rename scenarios

The `shop-dto` fixture covers the V1 field and rename operations:

```bash
voyager plan patch agent.patch
voyager plan add_field com.shop.OrderDTO giftMessage String
voyager plan remove_field com.shop.OrderDTO giftMessage
voyager plan rename_field com.shop.UserDTO.userName customerName
voyager plan rename_method com.shop.UserService.formatDisplayName formatCustomerLabel
voyager plan rename_class com.shop.UserDTO CustomerProfile
```

Run one scenario at a time from a fresh reset so each expected file list stays independent.

## E2E regression

Run the full example regression suite from the repository root:

```bash
python examples/e2e_v1.py
```

The script resets example projects, exercises `patch`, `add_field`,
`remove_field`, `rename_field`, `rename_method`, `rename_class`, and the
multi-project Server isolation flow, then stops any Servers it started.
