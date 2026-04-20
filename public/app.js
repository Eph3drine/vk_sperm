const authSection = document.getElementById("auth-section");
const appSection = document.getElementById("app-section");
const loginForm = document.getElementById("login-form");
const loginId = document.getElementById("login-id");
const loginPassword = document.getElementById("login-password");
const userLine = document.getElementById("user-line");
const weekLine = document.getElementById("week-line");
const dateInput = document.getElementById("date-input");
const lessonsBox = document.getElementById("lessons-box");
const logoutBtn = document.getElementById("logout-btn");
const headmanSection = document.getElementById("headman-section");
const headmanReport = document.getElementById("headman-report");
const refreshDayBtn = document.getElementById("refresh-day-btn");

let auth = {
  token: localStorage.getItem("token") || "",
  user: JSON.parse(localStorage.getItem("user") || "null")
};

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {})
  };

  if (auth.token) {
    headers.Authorization = `Bearer ${auth.token}`;
  }

  const response = await fetch(path, {
    ...options,
    headers
  });

  const raw = await response.text();
  let payload = {};
  try {
    payload = raw ? JSON.parse(raw) : {};
  } catch {
    payload = { error: raw || "Сервер вернул не JSON-ответ" };
  }

  if (!response.ok) {
    throw new Error(payload.error || `Ошибка запроса (${response.status})`);
  }
  return payload;
}

function setAuth(nextAuth) {
  auth = nextAuth;
  localStorage.setItem("token", auth.token || "");
  localStorage.setItem("user", JSON.stringify(auth.user || null));
}

function clearAuth() {
  auth = { token: "", user: null };
  localStorage.removeItem("token");
  localStorage.removeItem("user");
}

function todayISO() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

async function markAttendance(date, lessonIndex, present) {
  await api("/api/attendance", {
    method: "POST",
    body: JSON.stringify({ date, lessonIndex, present })
  });
}

async function loadMyAttendance(date) {
  const result = await api(`/api/attendance/my?date=${date}`);
  const map = new Map();
  for (const record of result.records) {
    map.set(record.lesson_index, record.present);
  }
  return map;
}

async function renderLessons() {
  const date = dateInput.value;
  if (!date) {
    return;
  }

  const scheduleData = await api(`/api/schedule?date=${date}`);
  weekLine.textContent = `Неделя: ${
    scheduleData.weekType === "numerator" ? "числитель" : "знаменатель"
  }`;

  const myMarks = await loadMyAttendance(date);
  lessonsBox.innerHTML = "";

  if (!scheduleData.lessons.length) {
    lessonsBox.innerHTML = "<p>На выбранную дату пар нет.</p>";
    return;
  }

  for (const lesson of scheduleData.lessons) {
    const row = document.createElement("div");
    row.className = "lesson-row";

    const state = myMarks.get(lesson.index);
    const stateText =
      typeof state === "boolean"
        ? `<span class="${state ? "present" : "absent"}">Текущая отметка: ${
            state ? "Был" : "Не был"
          }</span>`
        : "Пока не отмечено";

    row.innerHTML = `
      <p class="lesson-title">#${lesson.index} ${lesson.subject}</p>
      <p>${lesson.time}</p>
      <p>${stateText}</p>
      <div class="actions">
        <button data-lesson="${lesson.index}" data-present="true">Был</button>
        <button data-lesson="${lesson.index}" data-present="false" class="secondary">Не был</button>
      </div>
    `;

    row.querySelectorAll("button[data-lesson]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await markAttendance(date, Number(btn.dataset.lesson), btn.dataset.present === "true");
          await renderLessons();
          if (auth.user.role === "headman") {
            await renderHeadmanReport();
          }
        } catch (err) {
          alert(err.message);
        } finally {
          btn.disabled = false;
        }
      });
    });

    lessonsBox.appendChild(row);
  }
}

async function renderHeadmanReport() {
  if (!auth.user || auth.user.role !== "headman") {
    return;
  }

  const date = dateInput.value;
  if (!date) {
    return;
  }

  const [dayResult, groupResult, scheduleResult] = await Promise.all([
    api(`/api/attendance/day?date=${date}`),
    api("/api/group"),
    api(`/api/schedule?date=${date}`)
  ]);

  const { records } = dayResult;
  const students = groupResult.students || [];
  const lessons = scheduleResult.lessons || [];

  if (!lessons.length) {
    headmanReport.innerHTML = "<p>На выбранную дату пар нет, таблица не требуется.</p>";
    return;
  }

  const markMap = new Map();
  for (const rec of records) {
    markMap.set(`${rec.student_id}_${rec.lesson_index}`, rec.present);
  }

  const headCells = lessons
    .map((lesson) => `<th>#${lesson.index}<br />${lesson.subject}</th>`)
    .join("");

  const rows = students
    .map((student) => {
      const lessonCells = lessons
        .map((lesson) => {
          const mark = markMap.get(`${student.id}_${lesson.index}`);
          if (mark === true) {
            return `<td><span class="status-pill ok">Был</span></td>`;
          }
          if (mark === false) {
            return `<td><span class="status-pill no">Не был</span></td>`;
          }
          return `<td><span class="status-pill empty">Нет отметки</span></td>`;
        })
        .join("");

      return `
      <tr>
        <td>${student.fullName}</td>
        ${lessonCells}
      </tr>
      `;
    })
    .join("");

  headmanReport.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Студент</th>
            ${headCells}
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function showApp() {
  authSection.classList.add("hidden");
  appSection.classList.remove("hidden");
  userLine.textContent = `Пользователь: ${auth.user.fullName} (${auth.user.role})`;
  headmanSection.classList.toggle("hidden", auth.user.role !== "headman");
  dateInput.value = dateInput.value || todayISO();
}

function showLogin() {
  appSection.classList.add("hidden");
  authSection.classList.remove("hidden");
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({
        id: loginId.value.trim(),
        password: loginPassword.value.trim()
      })
    });
    setAuth({ token: result.token, user: result.user });
    showApp();
    await renderLessons();
    if (auth.user.role === "headman") {
      await renderHeadmanReport();
    }
  } catch (err) {
    alert(err.message);
  }
});

dateInput.addEventListener("change", async () => {
  try {
    await renderLessons();
    if (auth.user.role === "headman") {
      await renderHeadmanReport();
    }
  } catch (err) {
    alert(err.message);
  }
});

refreshDayBtn.addEventListener("click", async () => {
  try {
    await renderHeadmanReport();
  } catch (err) {
    alert(err.message);
  }
});

logoutBtn.addEventListener("click", () => {
  clearAuth();
  showLogin();
});

async function bootstrap() {
  if (!auth.token || !auth.user) {
    showLogin();
    return;
  }

  try {
    const { user } = await api("/api/me");
    setAuth({ token: auth.token, user });
    showApp();
    await renderLessons();
    if (auth.user.role === "headman") {
      await renderHeadmanReport();
    }
  } catch {
    clearAuth();
    showLogin();
  }
}

bootstrap();
