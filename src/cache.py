import aiosqlite
import json
from pathlib import Path
from typing import Optional, Dict

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
