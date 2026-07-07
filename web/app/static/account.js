// Вход в Zoom-аккаунт бота: форма → (OTP) → статус. Плюс ник по умолчанию.

function accEl(id) { return document.getElementById(id); }

const AC = {
  state: accEl("acc-state"),
  login: accEl("acc-login"),
  otpRow: accEl("acc-otp-row"),
  logged: accEl("acc-logged"),
  emailShown: accEl("acc-email-shown"),
  email: accEl("acc-email"),
  password: accEl("acc-password"),
  signin: accEl("acc-signin"),
  msg: accEl("acc-msg"),
  otp: accEl("acc-otp"),
  otpSubmit: accEl("acc-otp-submit"),
  otpMsg: accEl("acc-otp-msg"),
  nick: accEl("acc-nick"),
  nickSave: accEl("acc-nick-save"),
  nickMsg: accEl("acc-nick-msg"),
};

const ACC_LABELS = {
  logged_out: "не вошёл",
  signing_in: "вход…",
  waiting_otp: "нужен код",
  logged_in: "вошёл",
  error: "ошибка",
  unavailable: "недоступно",
};

function renderAcc(s) {
  const st = s && s.state ? s.state : "unavailable";
  AC.state.textContent =
    st === "logged_in" && s.email ? "вошёл: " + s.email : (ACC_LABELS[st] || st);
  AC.state.className =
    "pill " + (st === "logged_in" ? "pill-ok" : st === "waiting_otp" ? "pill-recording" : "");

  AC.login.style.display = st === "logged_in" ? "none" : "";
  AC.otpRow.style.display = st === "waiting_otp" ? "" : "none";
  AC.logged.style.display = st === "logged_in" ? "" : "none";
  if (st === "logged_in") AC.emailShown.textContent = s.email || "";

  AC.signin.disabled = st === "signing_in";
  if (st === "signing_in") {
    AC.msg.textContent = "вход… (до минуты)";
    AC.msg.className = "msg";
  } else if (st === "error" && s && s.error) {
    AC.msg.textContent = s.error;
    AC.msg.className = "msg err";
  } else if (st === "logged_out") {
    AC.msg.textContent = "";
  }
  if (st === "waiting_otp" && !AC.otpMsg.textContent) {
    AC.otpMsg.textContent = "код отправлен на почту аккаунта";
    AC.otpMsg.className = "msg";
  }
}

async function loadAcc() {
  try {
    const r = await fetch("/api/bot/account/status");
    renderAcc(await r.json());
  } catch (e) {
    renderAcc(null);
  }
}

AC.signin.addEventListener("click", async () => {
  const email = AC.email.value.trim();
  const password = AC.password.value;
  if (!email || !password) {
    AC.msg.textContent = "введите email и пароль";
    AC.msg.className = "msg err";
    return;
  }
  AC.msg.textContent = "вход…";
  AC.msg.className = "msg";
  try {
    const r = await fetch("/api/bot/account/sign-in", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const j = await r.json();
    if (j.error) {
      AC.msg.textContent = j.error;
      AC.msg.className = "msg err";
    } else {
      AC.password.value = "";
      renderAcc(j);
    }
  } catch (e) {
    AC.msg.textContent = "ошибка сети";
    AC.msg.className = "msg err";
  }
});

AC.otpSubmit.addEventListener("click", async () => {
  const code = AC.otp.value.trim();
  if (!code) {
    AC.otpMsg.textContent = "введите код";
    AC.otpMsg.className = "msg err";
    return;
  }
  AC.otpMsg.textContent = "проверка…";
  AC.otpMsg.className = "msg";
  try {
    const r = await fetch("/api/bot/account/otp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    const j = await r.json();
    if (j.error) {
      AC.otpMsg.textContent = j.error;
      AC.otpMsg.className = "msg err";
    } else {
      AC.otp.value = "";
      renderAcc(j);
    }
  } catch (e) {
    AC.otpMsg.textContent = "ошибка сети";
    AC.otpMsg.className = "msg err";
  }
});

// Ник по умолчанию — пока хранится в браузере; применяется при входе в митинг
// (полное связывание с join — в следующей фазе мультиворкера).
const NICK_KEY = "botDefaultNick";
AC.nick.value = localStorage.getItem(NICK_KEY) || "";
AC.nickSave.addEventListener("click", () => {
  localStorage.setItem(NICK_KEY, AC.nick.value.trim());
  AC.nickMsg.textContent = "сохранено ✓";
  AC.nickMsg.className = "msg ok";
  setTimeout(() => { AC.nickMsg.textContent = ""; }, 2000);
});

loadAcc();
setInterval(loadAcc, 4000);
