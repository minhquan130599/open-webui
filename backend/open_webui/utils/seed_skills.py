"""Install the bundled workspace skills once per deployment.

Each folder in SEED_SKILLS_DIR is one skill: the folder name is the skill id, SKILL.md is its content and any
other UTF-8 files are supporting files. A skill is created public, owned by the first admin, the first time
it is seen. Installed ids are recorded, so a skill an admin edits or deletes is left alone afterwards.
"""

import asyncio
import logging
import os
import re
from pathlib import Path

from open_webui.models.config import Config
from open_webui.models.skills import SkillForm, Skills
from open_webui.models.users import Users
from open_webui.utils.skill_files import frontmatter

log = logging.getLogger(__name__)

SEED_SKILLS_DIR = Path(os.getenv('SEED_SKILLS_DIR') or Path(__file__).resolve().parent.parent / 'seed_skills')
ENABLE_SEED_SKILLS = os.getenv('ENABLE_SEED_SKILLS', 'True').lower() == 'true'
SEEDED_KEY = 'seed.skills'
PUBLIC_READ_GRANT = {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}
RETRY_SECONDS = 60


def _display_name(skill_id: str, content: str) -> str:
    heading = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    return heading.group(1).strip() if heading else skill_id


def _read_skill(folder: Path) -> tuple[str, list[dict]]:
    content = (folder / 'SKILL.md').read_text(encoding='utf-8')
    files = [
        {'path': path.relative_to(folder).as_posix(), 'content': path.read_text(encoding='utf-8')}
        for path in sorted(folder.rglob('*'))
        if path.is_file()
    ]
    return content, files


async def seed_skills() -> bool:
    """Install pending skills. Returns False when it must retry later (no admin account yet)."""
    if not SEED_SKILLS_DIR.is_dir():
        return True
    seeded = set(await Config.get(SEEDED_KEY, []) or [])
    pending = [
        folder
        for folder in sorted(SEED_SKILLS_DIR.iterdir())
        if folder.is_dir() and (folder / 'SKILL.md').is_file() and folder.name not in seeded
    ]
    if not pending:
        return True

    owner = await Users.get_super_admin_user()
    if not owner:
        return False

    for folder in pending:
        skill_id = folder.name
        try:
            content, files = _read_skill(folder)
            name = _display_name(skill_id, content)
            if await Skills.get_skill_by_id(skill_id) or await Skills.get_skill_by_name(name):
                log.info('Seed skill %s already exists; leaving it unchanged', skill_id)
            else:
                await Skills.insert_new_skill(
                    owner.id,
                    SkillForm(
                        id=skill_id,
                        name=name,
                        description=str(frontmatter(content).get('description', '')),
                        files=files,
                        commit_message='Installed with the deployment',
                        access_grants=[PUBLIC_READ_GRANT],
                    ),
                )
                log.info('Installed seed skill %s', skill_id)
            seeded.add(skill_id)
        except Exception as e:
            # Another worker may have inserted it at the same moment; the next start records it.
            log.warning('Could not install seed skill %s: %s', skill_id, e)

    await Config.upsert({SEEDED_KEY: sorted(seeded)})
    return True


async def seed_skills_loop() -> None:
    """Run at startup; waits for the first admin account on a fresh install."""
    if not ENABLE_SEED_SKILLS:
        return
    while True:
        try:
            if await seed_skills():
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning('Seeding skills failed: %s', e)
            return
        await asyncio.sleep(RETRY_SECONDS)
