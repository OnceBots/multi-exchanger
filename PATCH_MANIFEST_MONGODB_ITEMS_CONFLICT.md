# MongoDB `items` path conflict fix

## Root cause
`MediaRepository.append_group()` used `$setOnInsert: {items: []}` and `$push: {items: item}` in the same update. MongoDB rejects this with write error code 40 (`Updating the path 'items' would create a conflict at 'items'`).

## Fix
Removed `items: []` from `$setOnInsert`. `$push` creates the array on an upsert when it does not exist, without a conflicting update path. Metadata and `$set.updated_at` remain unchanged.

## Regression tests
`tests/test_media_repository_append_group.py` checks the update document and confirms that album metadata is preserved while `items` is only written by `$push`.

## Validation
Run `python -m pytest -q tests/test_media_repository_append_group.py` from the repository root.
