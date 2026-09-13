import sqlite3, os, uuid
from contextlib import closing
DB=os.getenv('TESTS_DB_PATH','tests.sqlite3')

def init():
 with closing(sqlite3.connect(DB)) as c:
  c.execute('CREATE TABLE IF NOT EXISTS topics(id INTEGER PRIMARY KEY, name TEXT UNIQUE, active INTEGER DEFAULT 0)')
  c.execute('CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY, topic_id INTEGER, question TEXT, options TEXT, correct INTEGER, active INTEGER DEFAULT 1)')
  c.commit()

def topics(active_only=False):
 with closing(sqlite3.connect(DB)) as c:
  q='SELECT id,name,active FROM topics'+(' WHERE active=1' if active_only else '')+' ORDER BY id'
  return c.execute(q).fetchall()

def add_topic(name):
 init()
 with closing(sqlite3.connect(DB)) as c:
  c.execute('INSERT OR IGNORE INTO topics(name) VALUES(?)',(name,)); c.commit()
  return c.execute('SELECT id FROM topics WHERE name=?',(name,)).fetchone()[0]

def add_question(tid,q,opts,correct):
 import json
 with closing(sqlite3.connect(DB)) as c:
  c.execute('INSERT INTO questions(topic_id,question,options,correct) VALUES(?,?,?,?)',(tid,q,json.dumps(opts,ensure_ascii=False),correct)); c.commit()

def get_questions(active_only=True):
 import json
 with closing(sqlite3.connect(DB)) as c:
  rows=c.execute('SELECT q.id,t.name,q.question,q.options,q.correct FROM questions q JOIN topics t ON t.id=q.topic_id WHERE t.active=1 AND q.active=1').fetchall()
 return [(i,t,q,json.loads(o),int(c)) for i,t,q,o,c in rows]

def toggle_topic(tid):
 with closing(sqlite3.connect(DB)) as c:
  c.execute('UPDATE topics SET active=1-active WHERE id=?',(tid,)); c.commit()

def delete_topic(tid):
 with closing(sqlite3.connect(DB)) as c:
  c.execute('DELETE FROM questions WHERE topic_id=?',(tid,)); c.execute('DELETE FROM topics WHERE id=?',(tid,)); c.commit()
init()
