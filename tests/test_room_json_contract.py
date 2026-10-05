from pathlib import Path


def test_room_repo_hides_mongodb_object_id_from_public_results():
    source = Path('app/db/repositories/rooms.py').read_text(encoding='utf-8')
    assert '"_id": 0' in source
    assert '"$project": {"_id": 0}' in source
