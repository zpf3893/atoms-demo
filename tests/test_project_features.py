"""Project lifecycle and backup contracts; only temporary databases and offline models."""
import copy
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Store
from app.main import create_app

HTML = '<!doctype html><html><head><style>body{color:#333}</style></head><body><h1>可用应用</h1></body></html>'
HTML_TWO = HTML.replace('可用应用', '修改之后')


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(mode="mock", database=str(tmp_path / "features.db")))


def create(client, title="新项目"):
    response = client.post('/api/projects', json={"title": title})
    assert response.status_code == 201
    return response.json()


def save(client, project, html=HTML):
    response = client.post(f'/api/projects/{project["id"]}/versions', json={"html": html, "summary": "手动调整标题", "base_version_id": project["current_version"]})
    assert response.status_code == 201, response.text
    return response.json()


def test_manual_edits_conflicts_and_version_inspection(app):
    with TestClient(app) as client:
        initial = create(client)
        first = save(client, initial)
        second = save(client, first, HTML_TWO)
        assert second['source'] == 'manual'
        assert [v['number'] for v in second['versions']] == [2, 1]
        stale = client.post(f'/api/projects/{first["id"]}/versions', json={"html": HTML, "base_version_id": first['current_version']})
        assert stale.status_code == 409
        assert client.get(f'/api/projects/{first["id"]}').json()['html'] == HTML_TWO
        inspected = client.get(f'/api/projects/{first["id"]}/versions/{first["current_version"]}').json()
        assert inspected['html'] == HTML and inspected['source'] == 'manual'
        assert client.get(f'/api/projects/{first["id"]}/versions/missing').status_code == 404
        invalid = client.post(f'/api/projects/{first["id"]}/versions', json={"html": '<p>incomplete</p>', "base_version_id": second['current_version']})
        assert invalid.status_code == 422
        missing_base = client.post(f'/api/projects/{first["id"]}/versions', json={"html": HTML})
        assert missing_base.status_code == 422


def test_rename_archive_restore_and_list_metadata(app):
    with TestClient(app) as client:
        project = save(client, create(client))
        pid = project['id']
        rename = client.patch(f'/api/projects/{pid}', json={"title": " 我的效率工具 "})
        assert rename.json()['title'] == '我的效率工具'
        assert client.patch(f'/api/projects/{pid}', json={"title": '   '}).status_code == 422
        card = client.get('/api/projects').json()[0]
        assert card['version_count'] == 1 and card['source'] == 'manual'
        assert card['preview_html'] == HTML and card['archived'] is False
        assert client.post(f'/api/projects/{pid}/archive').json()['archived'] is True
        assert client.get('/api/projects').json() == []
        assert client.get('/api/projects?archived=true').json()[0]['id'] == pid
        assert client.get(f'/api/projects/{pid}/backup').status_code == 200
        assert client.patch(f'/api/projects/{pid}', json={'title': '不能编辑'}).status_code == 409
        assert client.put(f'/api/projects/{pid}/state', json={'value': {'x': 1}}).status_code == 409
        assert client.post(f'/api/projects/{pid}/generate', json={'prompt': '修改'}).status_code == 409
        assert client.post(f'/api/projects/{pid}/restore/{project["current_version"]}').status_code == 409
        assert client.post(f'/api/projects/{pid}/unarchive').json()['archived'] is False
        assert client.get('/api/projects').json()[0]['id'] == pid
        assert client.get(f'/api/projects/{pid}').json()['html'] == HTML


def test_permanent_delete_requires_archive_and_removes_project_data(app):
    with TestClient(app) as owner, TestClient(app) as other:
        project = save(owner, create(owner, '待清理项目'))
        pid = project['id']
        owner.put(f'/api/projects/{pid}/state', json={'value': {'entries': [1]}})
        assert owner.delete(f'/api/projects/{pid}').status_code == 409
        run = app.state.store.start_run(pid, owner.cookies.get('atoms_owner'), '一次已结束的生成', 20, 100)
        app.state.store.fail(run, '已停止', 'cancelled')
        assert owner.post(f'/api/projects/{pid}/archive').status_code == 200
        assert other.delete(f'/api/projects/{pid}').status_code == 404
        assert owner.delete(f'/api/projects/{pid}').status_code == 204
        assert owner.get(f'/api/projects/{pid}').status_code == 404
        assert owner.get('/api/projects?archived=true').json() == []
        with app.state.store.connect() as db:
            for table in ('projects', 'versions', 'messages', 'runs'):
                assert db.execute(f'SELECT COUNT(*) FROM {table} WHERE {"id" if table == "projects" else "project_id"}=?', (pid,)).fetchone()[0] == 0


def test_busy_mutations_do_not_race_generation(app):
    with TestClient(app) as client:
        project = save(client, create(client)); pid = project['id']
        owner = client.cookies.get('atoms_owner')
        run = app.state.store.start_run(pid, owner, '生成中', 20, 100)
        mutations = [
            ('PATCH', '', {'title': '改名'}),
            ('POST', '/archive', None), ('POST', '/unarchive', None),
            ('POST', '/duplicate', None),
            ('POST', f'/restore/{project["current_version"]}', None),
            ('POST', '/versions', {'html': HTML_TWO, 'base_version_id': project['current_version']}),
        ]
        for method, suffix, body in mutations:
            assert client.request(method, f'/api/projects/{pid}{suffix}', json=body).status_code == 409, suffix
        app.state.store.fail(run, '停止', 'cancelled')
        assert client.patch(f'/api/projects/{pid}', json={'title': '现在可改名'}).status_code == 200


def test_backup_round_trip_restored_current_and_independent_duplicate(app):
    with TestClient(app) as client:
        first = save(client, create(client, '业务应用'))
        second = save(client, first, HTML_TWO); pid = first['id']
        state = {'tasks': [{'id': 'one', 'text': '验收', 'done': False}]}
        client.put(f'/api/projects/{pid}/state', json={'value': state})
        client.post(f'/api/projects/{pid}/restore/{first["current_version"]}')
        response = client.get(f'/api/projects/{pid}/backup')
        assert response.status_code == 200 and 'attachment' in response.headers['content-disposition']
        backup = response.json()
        assert set(backup) == {'format', 'format_version', 'title', 'current_version', 'html', 'versions', 'app_state'}
        assert backup['app_state'] == state and backup['html'] == HTML
        assert client.cookies.get('atoms_owner') not in response.text
        imported_response = client.post('/api/projects/import', json=backup)
        assert imported_response.status_code == 201, imported_response.text
        imported = imported_response.json()
        assert imported['id'] != pid and imported['title'] == '业务应用'
        assert imported['html'] == HTML and len(imported['versions']) == 2
        assert not {v['id'] for v in imported['versions']} & {v['id'] for v in second['versions']}
        assert client.get(f'/api/projects/{imported["id"]}/state').json() == state
        duplicate = client.post(f'/api/projects/{pid}/duplicate').json()
        assert duplicate['title'] == '业务应用 · 副本' and duplicate['html'] == HTML
        assert client.get(f'/api/projects/{duplicate["id"]}/state').json() == state
        client.put(f'/api/projects/{duplicate["id"]}/state', json={'value': {'tasks': []}})
        assert client.get(f'/api/projects/{pid}/state').json() == state
        assert client.get(f'/api/projects/{imported["id"]}/state').json() == state


def test_empty_project_backup_round_trip(app):
    with TestClient(app) as client:
        original = create(client)
        backup = client.get(f'/api/projects/{original["id"]}/backup').json()
        imported = client.post('/api/projects/import', json=backup)
        assert imported.status_code == 201
        assert imported.json()['html'] == '' and imported.json()['versions'] == []


@pytest.mark.parametrize('mutation', ['marker', 'owner', 'invalid_html', 'missing_current', 'oversize_state', 'duplicate_version'])
def test_import_rejects_invalid_backups_atomically(app, mutation):
    with TestClient(app) as client:
        project = save(client, create(client))
        backup = client.get(f'/api/projects/{project["id"]}/backup').json()
        if mutation == 'marker': backup['format'] = 'different'
        if mutation == 'owner': backup['owner'] = 'another-owner'
        if mutation == 'invalid_html': backup['versions'][0]['html'] = '<p>broken</p>'
        if mutation == 'missing_current': backup['current_version'] = 'missing'
        if mutation == 'oversize_state': backup['app_state'] = {'x': 'a' * 20001}
        if mutation == 'duplicate_version': backup['versions'].append(copy.deepcopy(backup['versions'][0]))
        assert client.post('/api/projects/import', json=backup).status_code == 422
        assert len(client.get('/api/projects').json()) == 1


def test_import_rejects_oversized_body_before_parsing(app):
    with TestClient(app) as client:
        assert client.post('/api/projects/import', content=b'x' * 20_000_001).status_code == 413


@pytest.mark.parametrize('invalid_number', ['NaN', 'Infinity', '-Infinity'])
def test_nonfinite_state_is_rejected_without_poisoning_saved_data(app, invalid_number):
    with TestClient(app) as client:
        project = create(client)
        path = f'/api/projects/{project["id"]}'
        saved = {'tasks': [{'text': '保留已有内容', 'done': False}]}
        assert client.put(path + '/state', json={'value': saved}).status_code == 200
        invalid = '{"value":{"nested":[{"amount":' + invalid_number + '}]}}'
        response = client.put(path + '/state', content=invalid, headers={'Content-Type': 'application/json'})
        assert response.status_code == 422
        assert client.get(path + '/state').json() == saved

        backup = client.get(path + '/backup').json()
        backup['app_state'] = {'nested': [{'amount': float(invalid_number)}]}
        imported = client.post('/api/projects/import', content=json.dumps(backup), headers={'Content-Type': 'application/json'})
        assert imported.status_code == 422
        assert len(client.get('/api/projects').json()) == 1
        assert client.get(path + '/backup').json()['app_state'] == saved


def test_new_project_endpoints_remain_owner_scoped(app):
    with TestClient(app) as owner, TestClient(app) as other:
        project = save(owner, create(owner)); pid = project['id']
        requests = [
            ('PATCH', '', {'title': '冒用'}), ('POST', '/duplicate', None),
            ('POST', '/archive', None), ('POST', '/unarchive', None),
            ('GET', '/backup', None), ('GET', f'/versions/{project["current_version"]}', None),
            ('POST', '/versions', {'html': HTML, 'base_version_id': project['current_version']}),
        ]
        for method, suffix, body in requests:
            assert other.request(method, f'/api/projects/{pid}{suffix}', json=body).status_code == 404
        assert other.get('/api/projects?archived=true').json() == []
        other_project = save(other, create(other))
        assert other.get(f'/api/projects/{other_project["id"]}/versions/{project["current_version"]}').status_code == 404


def test_templates_work_without_api_key_and_keep_state_independent(tmp_path):
    class NeverCalled:
        async def complete(self, *args, **kwargs):
            raise AssertionError('Templates must never call a model')
    app = create_app(Settings(mode='deepseek', api_key='', database=str(tmp_path / 'templates.db')), NeverCalled())
    with TestClient(app) as client:
        templates = client.get('/api/templates').json()
        assert len(templates) >= 3
        first = client.post('/api/projects/from-template', json={'template_id': templates[0]['id']})
        assert first.status_code == 201, first.text
        project = first.json()
        assert project['source'] == 'template' and project['mode'] == 'template'
        assert project['html'] and project['last_run'] is None
        client.put(f'/api/projects/{project["id"]}/state', json={'value': {'custom': True}})
        second = client.post('/api/projects/from-template', json={'template_id': templates[0]['id']}).json()
        assert client.get(f'/api/projects/{second["id"]}/state').json() == {}
        assert client.post('/api/projects/from-template', json={'template_id': 'missing'}).status_code == 404


def test_first_generation_respects_custom_project_title(app):
    with TestClient(app) as client:
        project = create(client)
        client.patch(f'/api/projects/{project["id"]}', json={'title': '我的业务命名'})
        response = client.post(f'/api/projects/{project["id"]}/generate', json={'prompt': '待办清单'})
        assert '"type": "done"' in response.text
        assert client.get(f'/api/projects/{project["id"]}').json()['title'] == '我的业务命名'


def test_download_ignores_fake_head_inside_comment(app):
    with TestClient(app) as client:
        html = HTML.replace('<html>', '<html>\n<!-- example <head> placeholder -->\n')
        project = save(client, create(client), html)
        download = client.get(f'/api/projects/{project["id"]}/download')
        assert download.status_code == 200
        assert '<!-- example <head> placeholder -->' in download.text
        assert '<head><script>window.demoStorage=' in download.text
        assert download.text.count('window.demoStorage=') == 1


def test_additive_migration_preserves_existing_versions_and_business_data(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE projects (id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, current_version TEXT, app_state TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE versions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
              number INTEGER NOT NULL, html TEXT NOT NULL, prompt TEXT NOT NULL, summary TEXT NOT NULL,
              mode TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(project_id, number));
        """)
        db.execute('INSERT INTO projects VALUES(?,?,?,?,?,?,?)', ('old-p', 'owner', '旧项目', 'before', 'before', 'old-v', '{"kept":true}'))
        db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?,?)', ('old-v', 'old-p', 1, HTML, '需求', '摘要', 'mock', 'before'))
    store = Store(str(path))
    project = store.get('old-p', 'owner')
    assert project['title'] == '旧项目' and project['current_version'] == 'old-v'
    assert project['html'] == HTML and project['archived'] is False
    assert project['versions'][0]['source'] == 'generated'
    assert store.state('old-p') == {'kept': True}
    store.save_version('old-p', HTML_TWO, '调整', 'old-v')
    assert len(Store(str(path)).get('old-p', 'owner')['versions']) == 2
