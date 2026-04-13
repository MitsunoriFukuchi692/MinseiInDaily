// === 介護支援ボット care_new.js（介護士専用モード） ===

let currentLang = 'ja';
let conversationLog = [];

// === 言語選択（介護士の母国語） ===
document.querySelectorAll('.lang-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.lang-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentLang = btn.dataset.lang;
  });
});

// === メッセージ追加 ===
function sendMessage(role) {
  const inputId = role === 'caregiver' ? 'caregiver-input' : 'caree-input';
  const input = document.getElementById(inputId);
  const text = input.value.trim();
  if (!text) return;

  appendMessage(role, text);
  conversationLog.push({ role, text, time: new Date().toLocaleTimeString('ja-JP') });
  input.value = '';
  input.focus();
}

function appendMessage(role, text) {
  const chatWindow = document.getElementById('chat-window');
  const welcome = chatWindow.querySelector('.welcome-msg');
  if (welcome) welcome.remove();

  const div = document.createElement('div');
  div.className = `message ${role}`;
  const roleName = role === 'caregiver' ? '介護士' : '被介護者';
  div.innerHTML = `<div class="role-name">${roleName}</div>${escapeHtml(text)}`;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// === 音声入力 ===
function startRecognition(inputId, lang) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { alert('このブラウザは音声入力に対応していません。Chromeをお使いください。'); return; }

  const langMap = { ja: 'ja-JP', en: 'en-US', tl: 'fil-PH', id: 'id-ID', vi: 'vi-VN' };
  const recognition = new SR();
  recognition.lang = langMap[lang] || 'ja-JP';
  recognition.interimResults = false;

  const btnId = inputId === 'caregiver-input' ? 'mic-caregiver-btn' : 'mic-caree-btn';
  const btn = document.getElementById(btnId);
  if (btn) btn.classList.add('recording');

  recognition.start();

  recognition.onresult = (e) => {
    document.getElementById(inputId).value = e.results[0][0].transcript;
    if (btn) btn.classList.remove('recording');
  };
  recognition.onerror = () => { if (btn) btn.classList.remove('recording'); };
  recognition.onend   = () => { if (btn) btn.classList.remove('recording'); };
}

// === Enterキーで送信 ===
document.getElementById('caregiver-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') sendMessage('caregiver');
});
document.getElementById('caree-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') sendMessage('caree');
});

// === テンプレート対話 ===
const templates = [
  'おはようございます。今日の体調はいかがですか？',
  'お薬の時間です。飲みましたか？',
  'トイレに行きたいですか？',
  '食事の時間です。何か食べたいものはありますか？',
  '少し歩いてみましょうか？',
  '痛いところはありますか？',
  'ゆっくり休みましょう。',
  '家族の方から連絡がありましたよ。',
];

const templateBtn = document.getElementById('template-btn');
const templatePanel = document.getElementById('template-panel');
const templateButtons = document.getElementById('template-buttons');

templates.forEach(text => {
  const btn = document.createElement('button');
  btn.className = 'template-item';
  btn.textContent = text;
  btn.onclick = () => {
    document.getElementById('caregiver-input').value = text;
    document.getElementById('caregiver-input').focus();
    templatePanel.style.display = 'none';
  };
  templateButtons.appendChild(btn);
});

templateBtn.addEventListener('click', () => {
  const isVisible = templatePanel.style.display !== 'none';
  templatePanel.style.display = isVisible ? 'none' : 'block';
  if (!isVisible) {
    document.getElementById('translate-panel').style.display = 'none';
    document.getElementById('report-panel').style.display = 'none';
  }
});

// === 翻訳して読み上げ ===
const translateBtn = document.getElementById('translate-btn');
const translatePanel = document.getElementById('translate-panel');
const doTranslateBtn = document.getElementById('do-translate-btn');
const translationResult = document.getElementById('translation-result');

translateBtn.addEventListener('click', () => {
  const isVisible = translatePanel.style.display !== 'none';
  translatePanel.style.display = isVisible ? 'none' : 'block';
  if (!isVisible) {
    document.getElementById('template-panel').style.display = 'none';
    document.getElementById('report-panel').style.display = 'none';
  }
});

doTranslateBtn.addEventListener('click', async () => {
  const lastMsg = conversationLog[conversationLog.length - 1];
  if (!lastMsg) {
    translationResult.textContent = '先に会話を入力してください。';
    return;
  }

  const direction = document.getElementById('translate-direction').value;
  const [fromLang, toLang] = direction.split('-');

  doTranslateBtn.textContent = '翻訳中...';
  doTranslateBtn.disabled = true;

  try {
    const res = await fetch('/translate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: lastMsg.text, from: fromLang, to: toLang })
    });
    const data = await res.json();
    const translated = data.translated || data.text || 'エラーが発生しました';
    translationResult.textContent = translated;
    await speakText(translated, toLang);
  } catch (err) {
    translationResult.textContent = 'エラーが発生しました: ' + err.message;
  } finally {
    doTranslateBtn.textContent = '翻訳する';
    doTranslateBtn.disabled = false;
  }
});

// === 音声読み上げ ===
async function speakText(text, lang) {
  try {
    const res = await fetch('/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, lang })
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const audio = document.getElementById('audio-player');
    audio.src = url;
    audio.play().catch(() => {});
  } catch (err) {
    console.error('TTS error:', err);
  }
}

// === ファイルダウンロード共通関数（サーバー経由） ===
async function downloadFile(content, filename) {
  try {
    const res = await fetch('/download-log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, filename })
    });
    if (!res.ok) throw new Error('サーバーエラー');
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
    alert('保存に失敗しました: ' + err.message);
  }
}

// === 会話ログ保存 ===
document.getElementById('save-log-btn').addEventListener('click', async () => {
  if (conversationLog.length === 0) {
    alert('保存する会話がありません。');
    return;
  }
  const lines = conversationLog.map(entry => {
    const role = entry.role === 'caregiver' ? '介護士' : '被介護者';
    return `[${entry.time}] ${role}: ${entry.text}`;
  });
  const date = new Date().toLocaleDateString('ja-JP').replace(/\//g, '-');
  const content = `介護支援ボット 会話ログ\n日付: ${date}\n\n` + lines.join('\n');
  await downloadFile(content, `会話ログ_${date}.txt`);
});

// === 日報をインラインで生成 ===
const reportBtn = document.getElementById('report-btn');
const reportPanel = document.getElementById('report-panel');
const reportLoading = document.getElementById('report-loading');
const reportContent = document.getElementById('report-content');

reportBtn.addEventListener('click', async () => {
  if (conversationLog.length === 0) {
    alert('会話を入力してから日報を生成してください。');
    return;
  }

  const isVisible = reportPanel.style.display !== 'none';
  if (isVisible) {
    reportPanel.style.display = 'none';
    return;
  }
  reportPanel.style.display = 'block';
  document.getElementById('template-panel').style.display = 'none';
  document.getElementById('translate-panel').style.display = 'none';

  reportLoading.style.display = 'flex';
  reportContent.textContent = '';
  reportContent.style.display = 'none';

  try {
    const res = await fetch('/daily-report-inline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ log: conversationLog })
    });
    const data = await res.json();
    const text = data.report || 'エラーが発生しました。';

    reportLoading.style.display = 'none';
    reportContent.style.display = 'block';
    reportContent.textContent = text;

    reportPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) {
    reportLoading.style.display = 'none';
    reportContent.style.display = 'block';
    reportContent.textContent = 'エラーが発生しました: ' + err.message;
  }
});

// === コピーボタン ===
document.getElementById('copy-report-btn').addEventListener('click', () => {
  const text = reportContent.textContent;
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('copy-report-btn');
    btn.textContent = '✅ コピーしました';
    setTimeout(() => { btn.textContent = '📋 コピー'; }, 2000);
  });
});

// === 日報テキスト保存ボタン ===
document.getElementById('dl-report-btn').addEventListener('click', async () => {
  const text = reportContent.textContent;
  if (!text) return;
  const date = new Date().toLocaleDateString('ja-JP').replace(/\//g, '-');
  await downloadFile(text, `日報_${date}.txt`);
});
