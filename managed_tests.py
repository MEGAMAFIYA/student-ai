import sqlite3, os, json
from contextlib import closing

DB = os.getenv('TESTS_DB_PATH', 'tests.sqlite3')


def init():
    with closing(sqlite3.connect(DB)) as c:
        c.execute('CREATE TABLE IF NOT EXISTS topics(id INTEGER PRIMARY KEY, name TEXT UNIQUE, active INTEGER DEFAULT 0)')
        c.execute('CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY, topic_id INTEGER, question TEXT, options TEXT, correct INTEGER, active INTEGER DEFAULT 1)')
        c.commit()


def topics(active_only=False):
    with closing(sqlite3.connect(DB)) as c:
        q = 'SELECT id,name,active FROM topics' + (' WHERE active=1' if active_only else '') + ' ORDER BY id'
        return c.execute(q).fetchall()


def get_topic(tid):
    with closing(sqlite3.connect(DB)) as c:
        return c.execute('SELECT id,name,active FROM topics WHERE id=?', (tid,)).fetchone()


def get_topic_by_name(name):
    with closing(sqlite3.connect(DB)) as c:
        return c.execute('SELECT id,name,active FROM topics WHERE name=?', (name,)).fetchone()


def add_topic(name):
    """Yangi mavzu qo'shadi va uni darhol FAOL qiladi. Agar shu nomli mavzu
    allaqachon mavjud bo'lsa, uning holatiga tegmaydi — shunchaki id'sini
    qaytaradi (savol qo'shishni davom ettirish uchun)."""
    init()
    with closing(sqlite3.connect(DB)) as c:
        c.execute('INSERT OR IGNORE INTO topics(name,active) VALUES(?,1)', (name,))
        c.commit()
        return c.execute('SELECT id FROM topics WHERE name=?', (name,)).fetchone()[0]


def toggle_topic(tid):
    with closing(sqlite3.connect(DB)) as c:
        c.execute('UPDATE topics SET active=1-active WHERE id=?', (tid,))
        c.commit()


def delete_topic(tid):
    with closing(sqlite3.connect(DB)) as c:
        c.execute('DELETE FROM questions WHERE topic_id=?', (tid,))
        c.execute('DELETE FROM topics WHERE id=?', (tid,))
        c.commit()


def count_questions(tid):
    with closing(sqlite3.connect(DB)) as c:
        row = c.execute('SELECT COUNT(*) FROM questions WHERE topic_id=?', (tid,)).fetchone()
        return row[0] if row else 0


def add_question(tid, q, opts, correct):
    with closing(sqlite3.connect(DB)) as c:
        c.execute(
            'INSERT INTO questions(topic_id,question,options,correct,active) VALUES(?,?,?,?,1)',
            (tid, q, json.dumps(opts, ensure_ascii=False), correct),
        )
        c.commit()


def list_questions(tid):
    """Berilgan mavzudagi barcha savollarni (faol/nofaol farqisiz) qaytaradi —
    tahrirlash/o'chirish ro'yxati uchun."""
    with closing(sqlite3.connect(DB)) as c:
        rows = c.execute(
            'SELECT id,question,options,correct,active FROM questions WHERE topic_id=? ORDER BY id',
            (tid,),
        ).fetchall()
    return [(i, q, json.loads(o), int(corr), int(act)) for i, q, o, corr, act in rows]


def get_question(qid):
    with closing(sqlite3.connect(DB)) as c:
        row = c.execute(
            'SELECT id,topic_id,question,options,correct,active FROM questions WHERE id=?',
            (qid,),
        ).fetchone()
    if not row:
        return None
    i, tid, q, o, corr, act = row
    return (i, tid, q, json.loads(o), int(corr), int(act))


def update_question(qid, question=None, options=None, correct=None):
    sets, values = [], []
    if question is not None:
        sets.append('question=?'); values.append(question)
    if options is not None:
        sets.append('options=?'); values.append(json.dumps(options, ensure_ascii=False))
    if correct is not None:
        sets.append('correct=?'); values.append(correct)
    if not sets:
        return
    values.append(qid)
    with closing(sqlite3.connect(DB)) as c:
        c.execute(f'UPDATE questions SET {", ".join(sets)} WHERE id=?', values)
        c.commit()


def delete_question(qid):
    with closing(sqlite3.connect(DB)) as c:
        c.execute('DELETE FROM questions WHERE id=?', (qid,))
        c.commit()


def get_questions(active_only=True):
    with closing(sqlite3.connect(DB)) as c:
        rows = c.execute(
            'SELECT q.id,t.name,q.question,q.options,q.correct FROM questions q '
            'JOIN topics t ON t.id=q.topic_id WHERE t.active=1 AND q.active=1'
        ).fetchall()
    return [(i, t, q, json.loads(o), int(c)) for i, t, q, o, c in rows]


init()
