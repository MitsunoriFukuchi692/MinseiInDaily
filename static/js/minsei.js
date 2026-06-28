// === 民生委員かんたん日報アプリ ===

let supabase = null;
let currentToken = null;
let currentUser = null;
let currentResident = null;  // { id, name, address }
let accumulatedText = '';
let recognition = null;
let isRecording = false;

// ── 初期化 ──
document.addEventListener('DOMContentLoaded', async () => {
  console.log('DOMContentLoaded fired');
  console.log('window.supabase:', typeof window.supabase);
  // Supabaseクライアント初期化
  if (SUPABASE_URL && SUPABASE_KEY) {
    try {
      supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY);
      console.log('Supabase client created OK');
    } catch(e) {
      console.error('Supabase init error:', e);
      return;
    }

    // 既存セッションを確認
    const { data: { session } } = await supabase.auth.getSession();
    if (session) {
      currentToken = session.access_token;
      currentUser = session.user;
      onLoginSuccess();
    }
  }
});

// ── 画面切り替え ──
function showScreen(name) {
  stopTTS();
  document.querySelectorAll('.screen').forEach(s => s.style.display = 'none');
  document.getElementById('screen-' + name).style.display = 'block';
}

// ── ログイン ──
async function doLogin() {
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value.trim();
  const errEl = document.getElementById('login-error');
  const btn = document.getElementById('login-btn');

  errEl.style.display = 'none';
  if (!email || !password) {
    errEl.textContent = 'メールアドレスとパスワードを入力してください。';
    errEl.style.display = 'block';
    return;
  }

  btn.textContent = 'ログイン中...';
  btn.disabled = true;

  try {
    const { data, error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) throw error;
    currentToken = data.session.access_token;
    currentUser = data.user;
    onLoginSuccess();
  } catch (err) {
    errEl.textContent = 'ログインに失敗しました。メールアドレスまたはパスワードを確認してください。';
    errEl.style.display = 'block';
    btn.textContent = 'ログイン';
    btn.disabled = false;
  }
}

// Enterキーでログイン
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('login-password').addEventListener('keydown', e => {
    if (e.key === 'Enter') doLogin();
  });
});

function onLoginSuccess() {
  document.getElementById('logout-btn').style.display = 'block';
  showScreen('residents');
  loadResidents();
}

// ── ログアウト ──
async function logout() {
  if (!confirm('ログアウトしますか？')) return;
  await supabase.auth.signOut();
  currentToken = null;
  currentUser = null;
  currentResident = null;
  stopMic();
  document.getElementById('logout-btn').style.display = 'none';
  showScreen('login');
}

// ── 住民リスト読み込み ──
async function loadResidents() {
  const loadingEl = document.getElementById('residents-loading');
  const listEl = document.getElementById('residents-list');
  const emptyEl = document.getElementById('residents-empty');
  const nameEl = document.getElementById('commissioner-name-display');

  loadingEl.style.display = 'flex';
  listEl.innerHTML = '';
  emptyEl.style.display = 'none';

  if (currentUser) {
    const email = currentUser.email || '';
    nameEl.textContent = email + ' でログイン中';
  }

  try {
    const res = await fetch('/api/residents', {
      headers: { 'Authorization': 'Bearer ' + currentToken }
    });
    const data = await res.json();
    loadingEl.style.display = 'none';

    if (!res.ok || !data.residents || data.residents.length === 0) {
      emptyEl.style.display = 'block';
      return;
    }

    data.residents.forEach(r => {
      const card = document.createElement('div');
      card.className = 'resident-card';
      card.innerHTML = `
        <div class="resident-avatar">🏠</div>
        <div class="resident-info">
          <div class="resident-name">${escHtml(r.name)} さん</div>
          <div class="resident-address">${escHtml(r.address || '')}</div>
        </div>
        <div class="resident-arrow">›</div>
      `;
      card.addEventListener('click', () => selectResident(r));
      listEl.appendChild(card);
    });
  } catch (err) {
    loadingEl.style.display = 'none';
    emptyEl.textContent = '読み込みに失敗しました。再読み込みしてください。';
    emptyEl.style.display = 'block';
  }
}

// ── 住民選択 → 録音画面へ ──
function selectResident(resident) {
  currentResident = resident;
  accumulatedText = '';

  document.getElementById('recording-resident-name').textContent = resident.name + ' さん';
  document.getElementById('recording-date').textContent =
    '訪問日：' + new Date().toLocaleDateString('ja-JP', { year: 'numeric', month: 'long', day: 'numeric' });

  // 録音UIリセット
  document.getElementById('voice-text').value = '';
  document.getElementById('voice-card').style.display = 'none';
  document.getElementById('generate-btn').style.display = 'none';
  document.getElementById('mic-label').textContent = 'タップして録音開始';
  document.getElementById('mic-status').textContent = '';
  document.getElementById('mic-btn').classList.remove('recording');
  isRecording = false;

  showScreen('recording');
}

// ── マイクON/OFF ──
function toggleMic() {
  if (isRecording) {
    stopMic();
  } else {
    startMic();
  }
}

function startMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    alert('音声入力はChromeブラウザでご利用ください。');
    return;
  }

  recognition = new SR();
  recognition.lang = 'ja-JP';
  recognition.interimResults = true;
  recognition.continuous = true;

  const btn = document.getElementById('mic-btn');
  const label = document.getElementById('mic-label');
  const status = document.getElementById('mic-status');

  btn.classList.add('recording');
  label.textContent = 'タップして録音停止';
  status.textContent = '🔴 録音中... 話しかけてください';
  isRecording = true;

  let interimText = '';

  recognition.onresult = (e) => {
    interimText = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      if (e.results[i].isFinal) {
        accumulatedText += e.results[i][0].transcript;
      } else {
        interimText += e.results[i][0].transcript;
      }
    }
    const textarea = document.getElementById('voice-text');
    textarea.value = accumulatedText + interimText;

    // テキストがあれば入力欄と日報ボタンを表示
    if (accumulatedText || interimText) {
      document.getElementById('voice-card').style.display = 'block';
    }
  };

  recognition.onerror = (e) => {
    if (e.error !== 'no-speech') {
      status.textContent = '音声入力エラー: ' + e.error;
    }
  };

  recognition.onend = () => {
    // continuous=trueでも切れることがあるので、録音中なら再開
    if (isRecording) {
      try { recognition.start(); } catch (err) {}
    }
  };

  recognition.start();
}

function stopMic() {
  isRecording = false;
  if (recognition) {
    recognition.onend = null;
    recognition.stop();
    recognition = null;
  }

  const btn = document.getElementById('mic-btn');
  const label = document.getElementById('mic-label');
  const status = document.getElementById('mic-status');

  btn.classList.remove('recording');
  label.textContent = 'タップして録音開始';

  const finalText = document.getElementById('voice-text').value.trim();
  if (finalText) {
    accumulatedText = finalText;
    status.textContent = '✅ 録音完了。内容を確認して日報を生成してください。';
    document.getElementById('voice-card').style.display = 'block';
    document.getElementById('generate-btn').style.display = 'block';
  } else {
    status.textContent = '';
  }
}

function clearVoice() {
  accumulatedText = '';
  document.getElementById('voice-text').value = '';
  document.getElementById('voice-card').style.display = 'none';
  document.getElementById('generate-btn').style.display = 'none';
  document.getElementById('mic-status').textContent = '';
}

// ── AI日報生成 ──
async function generateReport() {
  const voiceText = document.getElementById('voice-text').value.trim();
  if (!voiceText) {
    alert('録音内容が空です。先に録音してください。');
    return;
  }
  if (!currentResident) return;

  // 日報画面へ移動してローディング表示
  showScreen('report');
  const today = new Date().toLocaleDateString('ja-JP', { year: 'numeric', month: 'long', day: 'numeric' });
  document.getElementById('report-meta').textContent =
    currentResident.name + ' さん　' + today;
  document.getElementById('report-loading').style.display = 'flex';
  document.getElementById('report-card').style.display = 'none';
  document.getElementById('report-actions').style.display = 'none';
  document.getElementById('save-status').style.display = 'none';

  try {
    const res = await fetch('/api/report/generate', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + currentToken,
      },
      body: JSON.stringify({
        voice_text: voiceText,
        resident_name: currentResident.name,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'エラーが発生しました');

    document.getElementById('report-loading').style.display = 'none';
    document.getElementById('report-content').value = data.report;
    document.getElementById('report-card').style.display = 'block';
    document.getElementById('report-actions').style.display = 'block';
  } catch (err) {
    document.getElementById('report-loading').style.display = 'none';
    document.getElementById('report-content').value = 'エラーが発生しました: ' + err.message;
    document.getElementById('report-card').style.display = 'block';
  }
}

// ── 日報を保存 ──
async function saveReport() {
  const report = document.getElementById('report-content').value.trim();
  if (!report || !currentResident) return;

  const statusEl = document.getElementById('save-status');
  statusEl.style.display = 'none';

  try {
    const res = await fetch('/api/report/save', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + currentToken,
      },
      body: JSON.stringify({
        resident_id: currentResident.id,
        voice_text: document.getElementById('voice-text') ? document.getElementById('voice-text').value : '',
        report: report,
        visited_at: new Date().toISOString().split('T')[0],
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'エラー');

    statusEl.className = 'save-status success';
    statusEl.textContent = '✅ 日報を保存しました。';
    statusEl.style.display = 'block';
  } catch (err) {
    statusEl.className = 'save-status error';
    statusEl.textContent = '保存に失敗しました: ' + err.message;
    statusEl.style.display = 'block';
  }
}

// ── テキストダウンロード ──
async function downloadReport() {
  const report = document.getElementById('report-content').value.trim();
  if (!report) return;

  const today = new Date().toLocaleDateString('ja-JP').replace(/\//g, '-');
  const filename = `訪問日報_${currentResident ? currentResident.name : ''}さん_${today}.txt`;
  const header = `民生委員 訪問日報\n訪問日：${today}\n対象者：${currentResident ? currentResident.name + ' さん' : ''}\n${'─'.repeat(30)}\n\n`;

  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: header + report, filename }),
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err) {
    alert('ダウンロードに失敗しました: ' + err.message);
  }
}

// ── コピー ──
async function copyReport() {
  const report = document.getElementById('report-content').value;
  if (!report) return;
  try {
    await navigator.clipboard.writeText(report);
    const btn = event.currentTarget;
    btn.textContent = '✅ コピーしました';
    setTimeout(() => { btn.textContent = '📋 コピー'; }, 2000);
  } catch (err) {
    alert('コピーに失敗しました。手動でコピーしてください。');
  }
}

// ── テンプレート対話 ──
function switchTab(tabId, btn) {
  document.querySelectorAll('.tmpl-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.template-items').forEach(p => p.style.display = 'none');
  btn.classList.add('active');
  document.getElementById('tmpl-' + tabId).style.display = 'flex';
}

function appendTemplate(text) {
  if (accumulatedText && !accumulatedText.endsWith('。') && !accumulatedText.endsWith('\n')) {
    accumulatedText += '。';
  }
  accumulatedText += text + '。';
  const textarea = document.getElementById('voice-text');
  textarea.value = accumulatedText;
  document.getElementById('voice-card').style.display = 'block';
  document.getElementById('generate-btn').style.display = 'block';
  document.getElementById('mic-status').textContent = '✅ テンプレートを追加しました。';
}

// ── 音声読み上げ ──
let ttsActive = false;
let ttsSpeakingBtn = null;

function stopTTS() {
  window.speechSynthesis.cancel();
  ttsActive = false;
  if (ttsSpeakingBtn) {
    ttsSpeakingBtn.textContent = '🔊 読み上げ';
    ttsSpeakingBtn.classList.remove('speaking');
    ttsSpeakingBtn = null;
  }
}

function speak(text, btn) {
  if (ttsActive) {
    const wasSameBtn = (ttsSpeakingBtn === btn);
    stopTTS();
    if (wasSameBtn) return;
  }
  if (!text.trim()) {
    alert('読み上げる内容がありません。');
    return;
  }
  if (!window.speechSynthesis) {
    alert('このブラウザは音声読み上げに対応していません。');
    return;
  }
  ttsSpeakingBtn = btn;
  ttsActive = true;
  btn.textContent = '⏹ 停止';
  btn.classList.add('speaking');

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'ja-JP';
  utterance.rate = 0.85;
  utterance.onend = stopTTS;
  utterance.onerror = stopTTS;
  window.speechSynthesis.speak(utterance);
}

function speakVoiceText() {
  speak(document.getElementById('voice-text').value, document.getElementById('tts-voice-btn'));
}

function speakReport() {
  speak(document.getElementById('report-content').value, document.getElementById('tts-report-btn'));
}

function escHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
