-- =====================================================
-- 民生委員かんたん日報アプリ Supabase テーブル設定
-- Supabase の SQL Editor でこのSQLを実行してください
-- =====================================================

-- 【1】住民テーブル
CREATE TABLE IF NOT EXISTS residents (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  commissioner_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  address         TEXT,
  notes           TEXT,
  is_active       BOOLEAN DEFAULT TRUE,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- RLS（行レベルセキュリティ）を有効化
ALTER TABLE residents ENABLE ROW LEVEL SECURITY;

-- 民生委員は自分の担当住民のみ参照・更新可能
CREATE POLICY "own_residents_select" ON residents
  FOR SELECT USING (commissioner_id = auth.uid());

CREATE POLICY "own_residents_insert" ON residents
  FOR INSERT WITH CHECK (commissioner_id = auth.uid());

CREATE POLICY "own_residents_update" ON residents
  FOR UPDATE USING (commissioner_id = auth.uid());

-- 【2】訪問日報テーブル
CREATE TABLE IF NOT EXISTS visit_reports (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resident_id     UUID REFERENCES residents(id) ON DELETE CASCADE,
  commissioner_id UUID REFERENCES auth.users(id),
  visited_at      DATE DEFAULT CURRENT_DATE,
  raw_voice_text  TEXT,
  full_report     TEXT,
  next_check      TEXT,
  status          TEXT NOT NULL DEFAULT '未対応' CHECK (status IN ('未対応', '対応中', '完了')),
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 既存環境向け：テーブルが既にある場合にカラムを追加する
ALTER TABLE visit_reports ADD COLUMN IF NOT EXISTS next_check TEXT;
ALTER TABLE visit_reports ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT '未対応';
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'visit_reports_status_check'
  ) THEN
    ALTER TABLE visit_reports ADD CONSTRAINT visit_reports_status_check
      CHECK (status IN ('未対応', '対応中', '完了'));
  END IF;
END $$;

ALTER TABLE visit_reports ENABLE ROW LEVEL SECURITY;

CREATE POLICY "own_reports_select" ON visit_reports
  FOR SELECT USING (commissioner_id = auth.uid());

CREATE POLICY "own_reports_insert" ON visit_reports
  FOR INSERT WITH CHECK (commissioner_id = auth.uid());

CREATE POLICY "own_reports_update" ON visit_reports
  FOR UPDATE USING (commissioner_id = auth.uid());

-- 【3】サイト閲覧数カウンター（120学会HPなど静的サイト用）
CREATE TABLE IF NOT EXISTS site_counters (
  site       TEXT PRIMARY KEY,
  count      BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLSを有効化し、ポリシーは作らない＝匿名キーからの直接アクセスは不可。
-- 更新・参照はサーバ（サービスキー）経由の関数のみに限定する。
ALTER TABLE site_counters ENABLE ROW LEVEL SECURITY;

-- 原子的に +1 して新しい値を返す（同時アクセスでも数え漏れしない）
CREATE OR REPLACE FUNCTION increment_site_counter(p_site TEXT)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  new_count BIGINT;
BEGIN
  INSERT INTO site_counters (site, count, updated_at)
  VALUES (p_site, 1, NOW())
  ON CONFLICT (site)
  DO UPDATE SET count = site_counters.count + 1, updated_at = NOW()
  RETURNING count INTO new_count;
  RETURN new_count;
END;
$$;

-- 【4】脳トレゲーム 挑戦記録（120学会HP braintrain 用）
CREATE TABLE IF NOT EXISTS brain_game_records (
  id         BIGSERIAL PRIMARY KEY,
  game       TEXT NOT NULL CHECK (game IN ('kiokusagashi', 'numberguess', 'natsukashi-shiritori', 'kotowaza-anaume', 'showa-crossword', 'nou-nenrei')),
  nickname   TEXT NOT NULL,
  score      INTEGER NOT NULL,
  cleared    BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS brain_game_records_game_created_idx
  ON brain_game_records (game, created_at DESC);

-- 既存DBには CREATE TABLE IF NOT EXISTS が効かないため、ゲーム追加時は
-- 下記マイグレーションを Supabase SQL エディタで実行して CHECK 制約を更新する。
ALTER TABLE brain_game_records DROP CONSTRAINT IF EXISTS brain_game_records_game_check;
ALTER TABLE brain_game_records ADD CONSTRAINT brain_game_records_game_check
  CHECK (game IN ('kiokusagashi', 'numberguess', 'natsukashi-shiritori', 'kotowaza-anaume', 'showa-crossword', 'nou-nenrei'));

-- RLSを有効化し、ポリシーは作らない＝匿名キーからの直接アクセスは不可。
-- 読み書きはサーバ（サービスキー）経由の関数のみに限定する。
ALTER TABLE brain_game_records ENABLE ROW LEVEL SECURITY;

-- 記録を1件追加する
CREATE OR REPLACE FUNCTION insert_brain_game_record(
  p_game TEXT, p_nickname TEXT, p_score INTEGER, p_cleared BOOLEAN
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO brain_game_records (game, nickname, score, cleared)
  VALUES (p_game, p_nickname, p_score, p_cleared);
END;
$$;

-- 記録を新しい順に取得する
CREATE OR REPLACE FUNCTION get_brain_game_records(p_game TEXT, p_limit INTEGER)
RETURNS SETOF brain_game_records
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT * FROM brain_game_records
  WHERE game = p_game
  ORDER BY created_at DESC
  LIMIT p_limit;
$$;

-- =====================================================
-- 【初期データ投入例】管理者がSupabase上で直接実行
-- commissioner_id には Supabase Auth の user.id を入れる
-- =====================================================
-- INSERT INTO residents (commissioner_id, name, address, notes)
-- VALUES
--   ('xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', '田中 花子', '知多市〇〇町1-2-3', ''),
--   ('xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', '鈴木 一郎', '知多市△△町4-5-6', ''),
--   ('xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', '佐藤 美代', '知多市□□町7-8-9', '');
