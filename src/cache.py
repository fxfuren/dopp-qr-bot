import aiosqlite
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List

class PDFCache:
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS pdf_cache (
                    remote_path TEXT PRIMARY KEY,
                    modified TEXT,
                    text TEXT,
                    spec_numbers TEXT,
                    vehicle_reg TEXT,
                    trailer_reg TEXT
                )
            ''')
            await db.commit()

    async def get_cached_pdf(self, remote_path: str, modified: str) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM pdf_cache WHERE remote_path = ?', (remote_path,)) as cursor:
                row = await cursor.fetchone()
                if row and row['modified'] == modified:
                    return {
                        'text': row['text'],
                        'spec_numbers': json.loads(row['spec_numbers']) if row['spec_numbers'] else [],
                        'vehicle_reg': row['vehicle_reg'],
                        'trailer_reg': row['trailer_reg']
                    }
        return None

    async def get_cached_by_vehicle(self, vehicle_reg: str, days: int = 30) -> List[Dict]:
        """
        Return all cached entries where vehicle_reg matches, modified within
        the last `days` days. Matching is case-insensitive, ignoring spaces
        and hyphens.

        Returns list of dicts with keys:
          remote_path, modified, text, spec_numbers, vehicle_reg, trailer_reg
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        # Normalise search term: upper, strip spaces/hyphens
        needle = vehicle_reg.upper().replace(' ', '').replace('-', '')

        results = []
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # Fetch all rows modified within the window that have a vehicle_reg
            async with db.execute(
                '''SELECT * FROM pdf_cache
                   WHERE vehicle_reg IS NOT NULL
                     AND modified >= ?
                   ORDER BY modified DESC''',
                (cutoff,)
            ) as cursor:
                async for row in cursor:
                    db_vehicle = row['vehicle_reg'].upper().replace(' ', '').replace('-', '')
                    if db_vehicle == needle:
                        results.append({
                            'remote_path': row['remote_path'],
                            'modified': row['modified'],
                            'text': row['text'],
                            'spec_numbers': json.loads(row['spec_numbers']) if row['spec_numbers'] else [],
                            'vehicle_reg': row['vehicle_reg'],
                            'trailer_reg': row['trailer_reg'],
                        })
        return results

    async def save_pdf_cache(self, remote_path: str, modified: str, text: str, spec_numbers: list, vehicle_reg: Optional[str], trailer_reg: Optional[str]):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT OR REPLACE INTO pdf_cache 
                (remote_path, modified, text, spec_numbers, vehicle_reg, trailer_reg)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                remote_path, 
                modified, 
                text, 
                json.dumps(spec_numbers) if spec_numbers else '[]', 
                vehicle_reg, 
                trailer_reg
            ))
            await db.commit()

