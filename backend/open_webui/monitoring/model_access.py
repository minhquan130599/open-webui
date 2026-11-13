"""Public / private access of models, changed from the monitor page.

Public = every user can read (use) the model: a ('user', '*', 'read') access grant. Private = only admins and
the users/groups the model is explicitly shared with. A model that comes straight from a connection has no
row in the model table until it is configured; making it public creates that row (like the default-access
registration does), so its other settings stay at their defaults.
"""

from open_webui.models.access_grants import AccessGrants
from open_webui.models.models import ModelForm, ModelMeta, ModelParams, Models
from open_webui.models.users import Users

PUBLIC = {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}


def _is_public(grants) -> bool:
    return any(g.principal_type == 'user' and g.principal_id == '*' and g.permission == 'read' for g in grants or [])


async def list_models(app) -> list[dict]:
    from open_webui.monitoring.reindex import internal_request
    from open_webui.utils.models import get_all_models

    try:
        available = await get_all_models(internal_request(app), refresh=False)
    except Exception:
        available = []
    rows = {m.id: m for m in await Models.get_all_models()}
    seen = set()
    result = []
    for model in available or []:
        model_id = model.get('id')
        if not model_id or model_id in seen or model.get('arena'):
            continue
        seen.add(model_id)
        row = rows.get(model_id)
        grants = row.access_grants if row else []
        info = model.get('info') or {}
        result.append(
            {
                'id': model_id,
                'name': model.get('name') or model_id,
                'connection': (model['urlIdx'] + 1) if isinstance(model.get('urlIdx'), int) else None,
                'custom': bool(info.get('base_model_id')),
                'base_model_id': info.get('base_model_id'),
                'configured': row is not None,
                'public': _is_public(grants),
                'shared': sum(1 for g in grants if not (g.principal_type == 'user' and g.principal_id == '*')),
                'active': info.get('is_active', True) if row else True,
            }
        )
    # Configured models whose connection is currently down are still listed, so they can be changed.
    for model_id, row in rows.items():
        if model_id not in seen:
            result.append(
                {
                    'id': model_id,
                    'name': row.name or model_id,
                    'connection': None,
                    'custom': bool(row.base_model_id),
                    'base_model_id': row.base_model_id,
                    'configured': True,
                    'public': _is_public(row.access_grants),
                    'shared': sum(
                        1 for g in row.access_grants if not (g.principal_type == 'user' and g.principal_id == '*')
                    ),
                    'active': row.is_active,
                    'unavailable': True,
                }
            )
    return result


async def set_public(app, model_id: str, public: bool, name: str = '') -> dict:
    row = await Models.get_model_by_id(model_id)
    if public:
        if row is None:
            owner = await Users.get_super_admin_user()
            if owner is None:
                raise ValueError('Không có tài khoản admin để làm chủ model')
            await Models.insert_new_model(
                ModelForm(
                    id=model_id,
                    base_model_id=None,
                    name=name or model_id,
                    meta=ModelMeta(),
                    params=ModelParams(),
                    access_grants=[PUBLIC],
                ),
                owner.id,
            )
        else:
            await AccessGrants.grant_access('model', model_id, 'user', '*', 'read')
    elif row is not None:
        await AccessGrants.revoke_access('model', model_id, 'user', '*', 'read')
    row = await Models.get_model_by_id(model_id)
    return {'id': model_id, 'public': _is_public(row.access_grants) if row else False}
