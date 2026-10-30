<template>
  <div class="app-layout">
    <!-- Верхняя навигационная панель -->
    <header class="top-nav">
      <div class="brand">
        <span class="logo-text">Nonconformist</span>
        <span class="sub-text">broadcast</span>
      </div>

      <div class="search-box">
        <span class="search-bracket">[Q]</span>
        <input type="text" placeholder="SEARCH DIRECTORY..." readonly />
      </div>

      <div class="auth-actions">
        <template v-if="currentUser">
          <span class="user-badge">{{ currentUser }}</span>
          <button class="nav-btn" @click="handleLogout">LOG OUT</button>
        </template>
        <template v-else>
          <button class="nav-btn" @click="openAuthModal('login')">LOG IN</button>
          <button class="nav-btn primary" @click="openAuthModal('register')">SIGN UP</button>
        </template>
      </div>
    </header>

    <div class="main-body">
      <!-- Левый сайдбар со списком стримов -->
      <aside class="sidebar">
        <nav class="nav-links">
          <div class="nav-item active">// HOME</div>
          <div class="nav-item">// BROWSE</div>
          <div class="nav-item">// FOLLOWING</div>
          <div class="nav-item">// ARCHIVE</div>
        </nav>

        <div class="sidebar-section-title">
          <span>RECOMMENDED</span>
          <span>{{ streamsList.length.toString().padStart(2, '0') }}</span>
        </div>

        <div class="stream-list">
          <div
            v-for="st in streamsList"
            :key="st.id"
            class="stream-item"
            :class="{ active: currentStream && currentStream.id === st.id }"
            @click="selectStream(st)"
          >
            <div class="avatar">{{ st.title.slice(0, 2).toUpperCase() }}</div>
            <div class="stream-meta">
              <span class="author">{{ st.title }}</span>
              <span class="category">{{ st.description || 'STREAM' }}</span>
            </div>
            <div class="live-pill" :class="{ is_live: st.is_live }">
              {{ st.is_live ? '■ LIVE' : 'OFFLINE' }}
            </div>
          </div>
        </div>
      </aside>

      <!-- Центральная сцена с плеером -->
      <main class="player-stage">
        <div class="video-container">
          <div class="status-overlay">
            <span class="badge">[LIVE] LL-HLS // 0.0s</span>
          </div>

          <video
            ref="videoElement"
            class="video-feed"
            controls
            autoplay
            muted
            playsinline
          ></video>

          <div v-if="!currentStream?.is_live" class="offline-placeholder">
            <span>// BROADCAST OFFLINE //</span>
            <small>WAITING FOR RTMP PUBLISHER</small>
          </div>
        </div>

        <!-- Информация о стриме под плеером -->
        <div class="broadcast-footer" v-if="currentStream">
          <div class="avatar large">
            {{ currentStream.title.slice(0, 2).toUpperCase() }}
          </div>
          <div class="info">
            <div class="title-row">
              <h2>{{ currentStream.title }}</h2>
              <span class="verified-tag">[VERIFIED]</span>
            </div>
            <p>{{ currentStream.description }}</p>
            <div class="tags">
              <span class="tag">FASTAPI</span>
              <span class="tag">LL-HLS</span>
              <span class="tag">MEDIAMTX</span>
            </div>
          </div>

          <div class="actions">
            <button class="action-btn">FOLLOW</button>
            <button class="action-btn primary">SUBSCRIBE</button>
            <div class="viewers">
              <span class="label">VIEWERS</span>
              <span class="value">{{ currentStream.viewers_count }}</span>
            </div>
          </div>
        </div>
      </main>

      <!-- Правый сайдбар чата -->
      <section class="chat-section">
        <div class="chat-header">
          <span>STREAM CHAT</span>
          <span class="sub-only">[SUB ONLY: OFF]</span>
        </div>

        <div class="chat-messages" ref="chatScrollContainer">
          <div v-for="msg in messages" :key="msg.id" class="chat-msg">
            <span class="time">{{ msg.time }}</span>
            <span class="author-tag" :class="msg.badge">{{ msg.author }}:</span>
            <span class="text">{{ msg.text }}</span>
          </div>
        </div>

        <div class="chat-input-area">
          <input
            v-model="inputMessage"
            @keyup.enter="sendMessage"
            type="text"
            placeholder="SEND A MESSAGE..."
          />
          <button @click="sendMessage">SEND</button>
        </div>
      </section>
    </div>

    <!-- Модальное окно входа / регистрации -->
    <div v-if="showAuthModal" class="modal-backdrop">
      <div class="modal">
        <h3>// {{ authMode.toUpperCase() }}</h3>
        <form @submit.prevent="submitAuth">
          <input v-model="authForm.username" type="text" placeholder="Username" required />
          <input
            v-if="authMode === 'register'"
            v-model="authForm.email"
            type="email"
            placeholder="Email"
            required
          />
          <input v-model="authForm.password" type="password" placeholder="Password" required />

          <div v-if="authError" class="modal-err">{{ authError }}</div>

          <div class="modal-actions">
            <button type="submit" class="action-btn primary">SUBMIT</button>
            <button type="button" class="action-btn" @click="showAuthModal = false">CANCEL</button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue';
import Hls from 'hls.js';

const CORE_API = 'http://localhost:8000';
const CHAT_WS_URL = 'ws://localhost:8001';

const videoElement = ref(null);
const streamsList = ref([]);
const currentStream = ref(null);
const messages = ref([]);
const inputMessage = ref('');
const chatScrollContainer = ref(null);

const currentUser = ref(localStorage.getItem('stream_user') || '');
const showAuthModal = ref(false);
const authMode = ref('login');
const authForm = ref({ username: '', email: '', password: '' });
const authError = ref('');

let hlsInstance = null;
let wsInstance = null;
let reconnectTimer = null;
let pollTimer = null;

// Динамическое получение активных стримов
const fetchStreams = async () => {
  try {
    const res = await fetch(`${CORE_API}/api/streams`);
    if (!res.ok) return;
    const data = await res.json();
    streamsList.value = data.items || [];

    if (streamsList.value.length > 0) {
      if (!currentStream.value) {
        const live = streamsList.value.find((s) => s.is_live);
        selectStream(live || streamsList.value[0]);
      } else {
        const updated = streamsList.value.find((s) => s.id === currentStream.value.id);
        if (updated) {
          const wasLive = currentStream.value.is_live;
          currentStream.value.is_live = updated.is_live;
          currentStream.value.playback_url = updated.playback_url;
          if (!wasLive && updated.is_live) {
            initPlayer(updated.playback_url);
          }
        }
      }
    }
  } catch (err) {
    console.error('Core API error:', err);
  }
};

// Запуск воспроизведения HLS без зависания LL буфера
const initPlayer = (url) => {
  if (!videoElement.value || !url) return;

  if (hlsInstance) {
    hlsInstance.destroy();
    hlsInstance = null;
  }

  if (Hls.isSupported()) {
    hlsInstance = new Hls({
      enableWorker: true,
      lowLatencyMode: false,
      backBufferLength: 30,
      maxBufferLength: 10,
      maxMaxBufferLength: 20,
      liveSyncDurationCount: 3,
      liveMaxLatencyDurationCount: 10
    });

    hlsInstance.loadSource(url);
    hlsInstance.attachMedia(videoElement.value);

    hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => {
      videoElement.value.play().catch(() => {
        videoElement.value.muted = true;
        videoElement.value.play();
      });
    });

    hlsInstance.on(Hls.Events.ERROR, (event, data) => {
      if (data.fatal) {
        switch (data.type) {
          case Hls.ErrorTypes.NETWORK_ERROR:
            hlsInstance.startLoad();
            break;
          case Hls.ErrorTypes.MEDIA_ERROR:
            hlsInstance.recoverMediaError();
            break;
          default:
            hlsInstance.destroy();
            break;
        }
      }
    });
  } else if (videoElement.value.canPlayType('application/vnd.apple.mpegurl')) {
    videoElement.value.src = url;
    videoElement.value.play();
  }
};

// Подключение к WebSocket чату с токеном авторизации
const initChat = (streamId) => {
  if (wsInstance) {
    wsInstance.close();
    wsInstance = null;
  }
  clearTimeout(reconnectTimer);

  const token = localStorage.getItem('stream_token');
  const wsUrl = token
    ? `${CHAT_WS_URL}/ws/chat/${streamId}?token=${encodeURIComponent(token)}`
    : `${CHAT_WS_URL}/ws/chat/${streamId}`;

  wsInstance = new WebSocket(wsUrl);

  wsInstance.onopen = () => {
    console.log(`[WS] Connected to stream room: ${streamId}`);
  };

  wsInstance.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      messages.value.push(data);
      nextTick(() => {
        if (chatScrollContainer.value) {
          chatScrollContainer.value.scrollTop = chatScrollContainer.value.scrollHeight;
        }
      });
    } catch (e) {
      console.error('[WS] Parse error', e);
    }
  };

  wsInstance.onclose = () => {
    reconnectTimer = setTimeout(() => {
      if (currentStream.value) initChat(currentStream.value.id);
    }, 3000);
  };
};

const selectStream = (st) => {
  currentStream.value = st;
  messages.value = [];
  initChat(st.id);
  initPlayer(st.playback_url);
};

const sendMessage = () => {
  const text = inputMessage.value.trim();
  if (!text || !wsInstance || wsInstance.readyState !== WebSocket.OPEN) return;

  wsInstance.send(JSON.stringify({ text }));
  inputMessage.value = '';
};

// Управление авторизацией
const openAuthModal = (mode) => {
  authMode.value = mode;
  authError.value = '';
  showAuthModal.value = true;
};

const submitAuth = async () => {
  authError.value = '';
  const endpoint =
    authMode.value === 'register'
      ? `${CORE_API}/api/v1/auth/register`
      : `${CORE_API}/api/v1/auth/login`;

  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(authForm.value)
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Authentication failed');
    }

    localStorage.setItem('stream_token', data.access_token);
    localStorage.setItem('stream_user', data.username);
    currentUser.value = data.username;
    showAuthModal.value = false;

    if (currentStream.value) {
      initChat(currentStream.value.id);
    }
  } catch (err) {
    authError.value = err.message;
  }
};

const handleLogout = () => {
  localStorage.removeItem('stream_token');
  localStorage.removeItem('stream_user');
  currentUser.value = '';
  if (currentStream.value) {
    initChat(currentStream.value.id);
  }
};

onMounted(() => {
  fetchStreams();
  pollTimer = setInterval(fetchStreams, 5000);
});

onBeforeUnmount(() => {
  clearInterval(pollTimer);
  clearTimeout(reconnectTimer);
  if (hlsInstance) hlsInstance.destroy();
  if (wsInstance) wsInstance.close();
});
</script>

<style scoped>
.app-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  width: 100vw;
  background-color: #0d0d0d;
  color: #ededed;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  overflow: hidden;
}

.top-nav {
  height: 54px;
  border-bottom: 1px solid #222;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  background-color: #121212;
}

.brand {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.logo-text {
  font-size: 20px;
  font-weight: 800;
  letter-spacing: -0.5px;
}

.sub-text {
  font-size: 13px;
  color: #777;
}

.search-box {
  display: flex;
  align-items: center;
  background: #1a1a1a;
  border: 1px solid #333;
  padding: 4px 12px;
  width: 380px;
}

.search-bracket {
  color: #555;
  margin-right: 8px;
}

.search-box input {
  background: transparent;
  border: none;
  outline: none;
  color: #aaa;
  width: 100%;
}

.auth-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.user-badge {
  font-size: 12px;
  color: #4ade80;
  padding: 4px 8px;
  border: 1px solid #222;
  background: #111;
}

.nav-btn {
  background: transparent;
  border: 1px solid #333;
  color: #ededed;
  padding: 6px 14px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.nav-btn.primary {
  background: #fff;
  color: #000;
  border-color: #fff;
}

.main-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.sidebar {
  width: 240px;
  border-right: 1px solid #222;
  background: #111;
  display: flex;
  flex-direction: column;
}

.nav-links {
  padding: 16px 0;
  border-bottom: 1px solid #222;
}

.nav-item {
  padding: 8px 20px;
  color: #777;
  font-size: 13px;
  cursor: pointer;
}

.nav-item.active,
.nav-item:hover {
  color: #fff;
  background: #161616;
}

.sidebar-section-title {
  display: flex;
  justify-content: space-between;
  padding: 12px 20px;
  font-size: 11px;
  color: #555;
  letter-spacing: 1px;
}

.stream-list {
  flex: 1;
  overflow-y: auto;
}

.stream-item {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  gap: 12px;
  cursor: pointer;
  border-left: 2px solid transparent;
}

.stream-item:hover,
.stream-item.active {
  background: #181818;
  border-left-color: #fff;
}

.avatar {
  width: 32px;
  height: 32px;
  background: #252525;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  font-size: 12px;
  border: 1px solid #333;
}

.avatar.large {
  width: 48px;
  height: 48px;
  font-size: 18px;
}

.stream-meta {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.stream-meta .author {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.stream-meta .category {
  font-size: 11px;
  color: #666;
}

.live-pill {
  font-size: 10px;
  color: #555;
}

.live-pill.is_live {
  color: #ff3333;
  font-weight: 800;
}

.player-stage {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #090909;
}

.video-container {
  flex: 1;
  position: relative;
  background: #000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.status-overlay {
  position: absolute;
  top: 14px;
  left: 14px;
  z-index: 10;
}

.status-overlay .badge {
  background: rgba(0, 0, 0, 0.7);
  border: 1px solid #333;
  padding: 4px 8px;
  font-size: 11px;
  color: #aaa;
}

.video-feed {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.offline-placeholder {
  position: absolute;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  color: #444;
  font-weight: 700;
}

.broadcast-footer {
  padding: 16px 24px;
  border-top: 1px solid #222;
  background: #111;
  display: flex;
  align-items: center;
  gap: 18px;
}

.broadcast-footer .info {
  flex: 1;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.title-row h2 {
  font-size: 16px;
  margin: 0;
}

.verified-tag {
  font-size: 11px;
  color: #888;
}

.broadcast-footer p {
  margin: 4px 0 8px;
  font-size: 12px;
  color: #777;
}

.tags {
  display: flex;
  gap: 6px;
}

.tag {
  font-size: 10px;
  background: #1c1c1c;
  border: 1px solid #292929;
  padding: 2px 6px;
  color: #aaa;
}

.actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.action-btn {
  background: transparent;
  border: 1px solid #333;
  color: #fff;
  padding: 8px 16px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.action-btn.primary {
  background: #fff;
  color: #000;
}

.viewers {
  margin-left: 12px;
  display: flex;
  flex-direction: column;
}

.viewers .label {
  font-size: 9px;
  color: #555;
}

.viewers .value {
  font-size: 15px;
  font-weight: 800;
}

.chat-section {
  width: 320px;
  border-left: 1px solid #222;
  background: #111;
  display: flex;
  flex-direction: column;
}

.chat-header {
  height: 48px;
  border-bottom: 1px solid #222;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  font-size: 12px;
  font-weight: 700;
}

.sub-only {
  font-size: 10px;
  color: #666;
}

.chat-messages {
  flex: 1;
  padding: 16px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.chat-msg {
  font-size: 12px;
  line-height: 1.4;
  word-break: break-word;
}

.chat-msg .time {
  color: #444;
  margin-right: 6px;
}

.author-tag {
  font-weight: 700;
  margin-right: 6px;
  color: #3b82f6;
}

.author-tag.SYS {
  color: #eab308;
}

.chat-input-area {
  padding: 12px 16px;
  border-top: 1px solid #222;
  display: flex;
  gap: 8px;
}

.chat-input-area input {
  flex: 1;
  background: #181818;
  border: 1px solid #292929;
  padding: 8px 10px;
  color: #fff;
  font-size: 12px;
  outline: none;
}

.chat-input-area button {
  background: #252525;
  border: 1px solid #333;
  color: #fff;
  padding: 0 14px;
  cursor: pointer;
  font-weight: 700;
  font-size: 11px;
}

.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.85);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.modal {
  background: #141414;
  border: 1px solid #333;
  padding: 24px;
  width: 320px;
}

.modal h3 {
  margin-top: 0;
  font-size: 14px;
}

.modal form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.modal input {
  background: #090909;
  border: 1px solid #292929;
  padding: 8px;
  color: #fff;
  outline: none;
}

.modal-err {
  color: #ef4444;
  font-size: 11px;
}

.modal-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
</style>