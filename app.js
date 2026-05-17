const STORAGE_KEY = "solo-leveling-fitness-v2";
const XP_PER_LEVEL = 5;
const RANKS = ["E", "D", "C", "B", "A", "S"];
let deferredInstallPrompt = null;

const quests = {
  upper: {
    name: "Upper / Push",
    type: "Main Quest",
    focus: "Στήθος, ώμοι, core support",
    exercises: ["Push-ups / push-up board", "Floor press", "Overhead press", "Halos"],
    weight: "8kg / 16kg / bodyweight",
  },
  legs: {
    name: "Legs + Core",
    type: "Main Quest",
    focus: "Πόδια, γλουτοί, κορμός",
    exercises: ["Goblet squat", "Sumo deadlift", "Plank", "Glute bridge"],
    weight: "16kg / bodyweight",
  },
  core: {
    name: "Core / Light",
    type: "Main Quest",
    focus: "Σταθερότητα, recovery, κίνηση",
    exercises: ["Plank", "Russian twist", "Halos", "Dead bug"],
    weight: "8kg / bodyweight",
  },
  recovery: {
    name: "Recovery",
    type: "Side Quest",
    focus: "Light activation χωρίς XP",
    exercises: ["Mobility", "Light halos", "Dead bug", "Easy plank"],
    weight: "Light",
  },
};

const initialState = {
  sessions: [
    {
      id: crypto.randomUUID(),
      date: today(),
      type: "Main Quest",
      exercise: "Upper / Push",
      weight: "8kg / 16kg διαθέσιμα",
      waist: "",
      notes: "Αρχικό confirmed session.",
    },
  ],
};

const state = loadState();

const elements = {
  rank: document.querySelector("#rankValue"),
  level: document.querySelector("#levelValue"),
  xp: document.querySelector("#xpValue"),
  streak: document.querySelector("#streakValue"),
  week: document.querySelector("#weekValue"),
  xpTrack: document.querySelector("#xpTrack"),
  nextLevel: document.querySelector("#nextLevelText"),
  shoulder: document.querySelector("#shoulderInput"),
  knee: document.querySelector("#kneeInput"),
  fatigue: document.querySelector("#fatigueInput"),
  generatedQuest: document.querySelector("#generatedQuest"),
  questTypeHint: document.querySelector("#questTypeHint"),
  quickLog: document.querySelector("#quickLogButton"),
  walkQuest: document.querySelector("#walkQuestButton"),
  date: document.querySelector("#dateInput"),
  type: document.querySelector("#typeInput"),
  exercise: document.querySelector("#exerciseInput"),
  weight: document.querySelector("#weightInput"),
  waist: document.querySelector("#waistInput"),
  notes: document.querySelector("#notesInput"),
  form: document.querySelector("#sessionForm"),
  mainCount: document.querySelector("#mainCountValue"),
  sideCount: document.querySelector("#sideCountValue"),
  walkCount: document.querySelector("#walkCountValue"),
  history: document.querySelector("#historyList"),
  reset: document.querySelector("#resetButton"),
  template: document.querySelector("#historyItemTemplate"),
  installPanel: document.querySelector("#installPanel"),
  installButton: document.querySelector("#installButton"),
};

let currentQuest = quests.legs;

function today() {
  return new Date().toISOString().slice(0, 10);
}

function loadState() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return structuredClone(initialState);

  try {
    return JSON.parse(raw);
  } catch {
    return structuredClone(initialState);
  }
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function getStats() {
  const main = state.sessions.filter((session) => session.type === "Main Quest").length;
  const side = state.sessions.filter((session) => session.type === "Side Quest").length;
  const walk = state.sessions.filter((session) => session.type === "Walk Quest").length;
  const level = Math.floor(main / XP_PER_LEVEL) + 1;
  const rank = RANKS[Math.min(Math.floor((level - 1) / 3), RANKS.length - 1)];
  const xp = main % XP_PER_LEVEL;
  const week = state.sessions.filter((session) => isThisWeek(session.date)).length;

  return {
    main,
    side,
    walk,
    level,
    rank,
    xp,
    week,
    streak: calculateStreak(state.sessions),
  };
}

function isThisWeek(value) {
  const target = new Date(`${value}T00:00:00`);
  const now = new Date();
  const day = now.getDay() || 7;
  const monday = new Date(now);
  monday.setDate(now.getDate() - day + 1);
  monday.setHours(0, 0, 0, 0);
  return target >= monday;
}

function calculateStreak(sessions) {
  const dates = new Set(sessions.map((session) => session.date));
  let streak = 0;
  const cursor = new Date();

  while (dates.has(cursor.toISOString().slice(0, 10))) {
    streak += 1;
    cursor.setDate(cursor.getDate() - 1);
  }

  return streak;
}

function chooseQuest() {
  const shoulder = elements.shoulder.value;
  const knee = elements.knee.value;
  const fatigue = elements.fatigue.value;

  if (shoulder === "pain" || knee === "pain" || fatigue === "high") {
    return quests.recovery;
  }

  if (knee === "careful") return shoulder === "careful" ? quests.core : quests.upper;
  if (shoulder === "careful") return quests.legs;

  const rotation = [quests.upper.name, quests.legs.name, quests.core.name];
  const lastMain = [...state.sessions].reverse().find((session) => session.type === "Main Quest");
  if (!lastMain) return quests.upper;

  const index = rotation.indexOf(lastMain.exercise);
  const nextName = rotation[(index + 1) % rotation.length] || rotation[0];
  return Object.values(quests).find((quest) => quest.name === nextName) || quests.upper;
}

function renderQuest() {
  currentQuest = chooseQuest();
  elements.questTypeHint.textContent = currentQuest.type;
  elements.quickLog.textContent =
    currentQuest.type === "Main Quest" ? "Log προτεινόμενο quest" : "Log recovery side quest";

  elements.generatedQuest.innerHTML = `
    <h3>${currentQuest.name}</h3>
    <p>${currentQuest.focus}</p>
    <ul>${currentQuest.exercises.map((exercise) => `<li>${exercise}</li>`).join("")}</ul>
  `;
}

function renderStats() {
  const stats = getStats();
  elements.rank.textContent = stats.rank;
  elements.level.textContent = stats.level;
  elements.xp.textContent = `${stats.xp} / ${XP_PER_LEVEL}`;
  elements.streak.textContent = stats.streak;
  elements.week.textContent = stats.week;
  elements.mainCount.textContent = stats.main;
  elements.sideCount.textContent = stats.side;
  elements.walkCount.textContent = stats.walk;
  elements.nextLevel.textContent = `${XP_PER_LEVEL - stats.xp} Main Quests μέχρι level up`;

  elements.xpTrack.innerHTML = Array.from({ length: XP_PER_LEVEL }, (_, index) => {
    const className = index < stats.xp ? "xp-cell filled" : "xp-cell";
    return `<span class="${className}"></span>`;
  }).join("");
}

function renderHistory() {
  const ordered = [...state.sessions].sort((a, b) => b.date.localeCompare(a.date));
  elements.history.innerHTML = "";

  if (!ordered.length) {
    elements.history.innerHTML = `<article class="history-item"><p>Δεν υπάρχει history ακόμα.</p></article>`;
    return;
  }

  ordered.forEach((session) => {
    const item = elements.template.content.cloneNode(true);
    item.querySelector(".history-title").textContent = `${session.date} · ${session.exercise}`;
    item.querySelector(".history-meta").textContent = `${session.type} · ${session.weight || "χωρίς βάρος"} · ${session.waist || "χωρίς μέση"}`;
    item.querySelector(".history-notes").textContent = session.notes || "";
    item.querySelector(".delete-button").addEventListener("click", () => {
      state.sessions = state.sessions.filter((entry) => entry.id !== session.id);
      saveState();
      render();
    });
    elements.history.appendChild(item);
  });
}

function addSession(session) {
  state.sessions.push({
    id: crypto.randomUUID(),
    date: today(),
    weight: "",
    waist: "",
    notes: "",
    ...session,
  });
  saveState();
  render();
}

function render() {
  renderStats();
  renderQuest();
  renderHistory();
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((button) => button.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.remove("active"));
    tab.classList.add("active");
    document.querySelector(`#${tab.dataset.tab}Panel`).classList.add("active");
  });
});

[elements.shoulder, elements.knee, elements.fatigue].forEach((control) => {
  control.addEventListener("change", renderQuest);
});

elements.quickLog.addEventListener("click", () => {
  addSession({
    type: currentQuest.type,
    exercise: currentQuest.name,
    weight: currentQuest.weight,
    notes: currentQuest.focus,
  });
});

elements.walkQuest.addEventListener("click", () => {
  addSession({
    type: "Walk Quest",
    exercise: "20 λεπτά περπάτημα",
    notes: "Off day tracking",
  });
});

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  addSession({
    date: elements.date.value,
    type: elements.type.value,
    exercise: elements.exercise.value.trim(),
    weight: elements.weight.value.trim(),
    waist: elements.waist.value.trim(),
    notes: elements.notes.value.trim(),
  });
  elements.form.reset();
  elements.date.value = today();
});

elements.reset.addEventListener("click", () => {
  const confirmed = confirm("Θες σίγουρα reset σε όλα τα fitness logs;");
  if (!confirmed) return;

  state.sessions = [];
  saveState();
  render();
});

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredInstallPrompt = event;
  elements.installButton.disabled = false;
});

elements.installButton.addEventListener("click", async () => {
  if (!deferredInstallPrompt) {
    alert("Στο κινητό: άνοιξε το menu του browser και πάτα Add to Home Screen.");
    return;
  }

  deferredInstallPrompt.prompt();
  await deferredInstallPrompt.userChoice;
  deferredInstallPrompt = null;
});

window.addEventListener("appinstalled", () => {
  elements.installPanel.style.display = "none";
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("./service-worker.js");
  });
}

elements.date.value = today();
render();
