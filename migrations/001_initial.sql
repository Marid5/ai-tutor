-- AI Tutor: initial schema.
--
-- Three groups of tables: accounts (who may sign in), course content (what is
-- taught, rewritten from the YAML program at every start) and per-learner
-- state (what each learner has done). Everything a learner owns is removed
-- with the learner (ON DELETE CASCADE), so deleting an account leaves nothing
-- behind. Content is never deleted, only marked retired, so history stays
-- readable after a card is dropped from the program.

-- ---------------------------------------------------------------- accounts
CREATE TABLE users (
  id TEXT PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- The browser holds a random token; only its hash is stored, so a copy of the
-- database cannot be turned into a signed-in session.
CREATE TABLE auth_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX idx_auth_sessions_user ON auth_sessions(user_id);
CREATE INDEX idx_auth_sessions_expires ON auth_sessions(expires_at);

-- Sign-in attempt log for rate limiting. It lives in the database rather than
-- in memory so a restart does not hand an attacker a fresh allowance.
CREATE TABLE rate_limit_hits (
  bucket TEXT NOT NULL,
  at TEXT NOT NULL
);
CREATE INDEX idx_rate_limit_hits_bucket ON rate_limit_hits(bucket, at);

-- ----------------------------------------------------------------- content
CREATE TABLE chapters (
  id TEXT PRIMARY KEY,
  position INTEGER NOT NULL,
  title TEXT NOT NULL,
  -- Which exercise kinds this chapter offers, with program defaults applied.
  exercises_json TEXT NOT NULL,
  retired INTEGER NOT NULL DEFAULT 0 CHECK (retired IN (0, 1))
);

CREATE TABLE lessons (
  id TEXT PRIMARY KEY,
  chapter_id TEXT NOT NULL REFERENCES chapters(id),
  position INTEGER NOT NULL,
  title TEXT NOT NULL,
  retired INTEGER NOT NULL DEFAULT 0 CHECK (retired IN (0, 1))
);
CREATE INDEX idx_lessons_chapter ON lessons(chapter_id, position);

-- One row per card. The columns after `source` are derived from the card and
-- its chapter when the program is loaded, so a request never has to re-run
-- the content rules: which exercises the card climbs, whether the triage
-- self-rating is on, and the material for the gap and block exercises.
CREATE TABLE cards (
  id TEXT PRIMARY KEY,
  lesson_id TEXT NOT NULL REFERENCES lessons(id),
  chapter_id TEXT NOT NULL REFERENCES chapters(id),
  -- Running position across the whole program, so one ORDER BY gives course order.
  position INTEGER NOT NULL,

  prompt TEXT NOT NULL,
  prompt_variants_json TEXT NOT NULL DEFAULT '[]',
  answer TEXT NOT NULL,
  option TEXT NOT NULL,                   -- the correct button (defaults to the answer)
  distractors_json TEXT NOT NULL DEFAULT '[]',
  accepted_json TEXT NOT NULL,            -- answer keys (defaults to the option)
  key_mode TEXT NOT NULL CHECK (key_mode IN ('any_of', 'all_of')),
  hint TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  tags_json TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL DEFAULT '',

  cloze_key TEXT,                         -- the span cut out of the answer, if a gap is possible
  cloze_options_json TEXT NOT NULL DEFAULT '[]',
  assemble_eligible INTEGER NOT NULL DEFAULT 0 CHECK (assemble_eligible IN (0, 1)),
  rungs_json TEXT NOT NULL DEFAULT '[]',  -- closed exercises this card climbs, in order
  triage_enabled INTEGER NOT NULL DEFAULT 1 CHECK (triage_enabled IN (0, 1)),

  -- Identity of the check a learner passes (answer material only). A change
  -- means earlier answers belong to a different question, so schedules reset.
  check_hash TEXT NOT NULL,
  -- How many times check_hash has changed since the card was first loaded.
  -- An edit that is later reverted brings the hash back but not the epoch.
  check_epoch INTEGER NOT NULL DEFAULT 0,
  -- sha256 of "<check_hash>:<check_epoch>": identifies one version of the
  -- check. Answers are pinned to it, and step ids carry its first 8 chars.
  check_version TEXT NOT NULL,
  program_version TEXT NOT NULL,
  retired INTEGER NOT NULL DEFAULT 0 CHECK (retired IN (0, 1))
);
CREATE INDEX idx_cards_lesson ON cards(lesson_id, position);
CREATE INDEX idx_cards_chapter ON cards(chapter_id, position);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- ------------------------------------------------------- per-learner state
-- Composite primary key: a schedule belongs to one (learner, card) pair.
CREATE TABLE card_state (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  card_id TEXT NOT NULL REFERENCES cards(id),
  due TEXT NOT NULL,
  stability REAL,
  difficulty REAL,
  reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'new',
  last_review TEXT,
  -- The check the schedule was earned against.
  check_hash TEXT,
  PRIMARY KEY (user_id, card_id)
);
CREATE INDEX idx_card_state_due ON card_state(user_id, state, due);

-- Every accepted answer. Readiness, the exercise ladder and "already checked"
-- are derived from events that carry the card's current check_version, so a
-- card whose answer changed starts over without its history being deleted,
-- and still starts over if the edit is later reverted.
CREATE TABLE events (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  session_id TEXT NOT NULL,
  card_id TEXT,
  kind TEXT NOT NULL,
  answer TEXT NOT NULL DEFAULT '',
  rating TEXT,
  correct INTEGER,
  elapsed_ms INTEGER NOT NULL DEFAULT 0,
  timing_version INTEGER,
  step_id TEXT,
  check_version TEXT NOT NULL
);
-- Idempotency: a replayed answer must not be counted twice.
CREATE UNIQUE INDEX events_user_session_step_unique
  ON events(user_id, session_id, step_id) WHERE step_id IS NOT NULL;
-- A remediation step keeps one identity across sessions, so its uniqueness is
-- per learner rather than per session. The `v2:` prefix marks such steps.
CREATE UNIQUE INDEX events_user_v2_step_unique
  ON events(user_id, step_id) WHERE step_id LIKE 'v2:%';
CREATE INDEX idx_events_user_card ON events(user_id, card_id);
CREATE INDEX idx_events_user_session ON events(user_id, session_id);

CREATE TABLE user_lesson_state (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  lesson_id TEXT NOT NULL REFERENCES lessons(id),
  status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed')),
  started_at TEXT,
  completed_at TEXT,
  accuracy REAL,
  PRIMARY KEY (user_id, lesson_id)
);

CREATE TABLE user_meta (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY (user_id, key)
);

-- Scheduled review (sliced into fixed-size sittings) and voluntary practice.
-- The CHECKs reject an unknown mode or status instead of storing it silently.
CREATE TABLE study_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mode TEXT NOT NULL CHECK (mode IN ('scheduled_review', 'lesson_practice', 'mixed_practice')),
  day_key TEXT NOT NULL,
  card_ids_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'abandoned')),
  started_at TEXT NOT NULL,
  completed_at TEXT,
  slice_index INTEGER NOT NULL DEFAULT 0,
  -- Only lesson practice sets this: the lesson being replayed.
  lesson_id TEXT REFERENCES lessons(id)
);
CREATE INDEX idx_study_sessions_user_status ON study_sessions(user_id, status, started_at);
CREATE UNIQUE INDEX idx_study_sessions_review_slice
  ON study_sessions(user_id, day_key, slice_index) WHERE mode = 'scheduled_review';
-- One live practice session per learner; scheduled review keeps its own
-- slice-based uniqueness above.
CREATE UNIQUE INDEX idx_study_sessions_practice_single
  ON study_sessions(user_id)
  WHERE status = 'in_progress' AND mode IN ('lesson_practice', 'mixed_practice');
