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
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE visit_reports ENABLE ROW LEVEL SECURITY;

CREATE POLICY "own_reports_select" ON visit_reports
  FOR SELECT USING (commissioner_id = auth.uid());

CREATE POLICY "own_reports_insert" ON visit_reports
  FOR INSERT WITH CHECK (commissioner_id = auth.uid());

-- =====================================================
-- 【初期データ投入例】管理者がSupabase上で直接実行
-- commissioner_id には Supabase Auth の user.id を入れる
-- =====================================================
-- INSERT INTO residents (commissioner_id, name, address, notes)
-- VALUES
--   ('xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', '田中 花子', '知多市〇〇町1-2-3', ''),
--   ('xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', '鈴木 一郎', '知多市△△町4-5-6', ''),
--   ('xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', '佐藤 美代', '知多市□□町7-8-9', '');
