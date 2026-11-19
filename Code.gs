/**
 * Напоминалка: отправка писем по расписанию и ежемесячная статистика нажатий.
 * Скрипт принимает от приложения напоминания и раз в 5 минут проверяет,
 * не пора ли отправить письмо. Письмо уходит на адрес, который указан в
 * напоминании. Если адрес не указан, письмо уходит владельцу скрипта.
 * 1-го числа каждого месяца в 8 утра присылает статистику нажатий за прошлый месяц.
 *
 * 1) Замените СЕКРЕТ на свой длинный набор букв и цифр (его же введёте в приложении).
 * 2) Один раз запустите функцию  setup  (кнопка «Выполнить») и разрешите доступ.
 * 3) Для проверки запустите функцию  testMail  — на вашу почту должно прийти тестовое письмо.
 * 4) Развёртывание → Управление развёртываниями → карандаш → Версия: «Новая версия» → Развернуть.
 *    Адрес (/exec) при этом не меняется. Если создаёте новое развёртывание, адрес изменится,
 *    и его нужно вставить в приложение заново.
 *    «Запускать от имени» — Я,  «У кого есть доступ» — Все.
 */
const SECRET = '27111977';
const APP_URL = 'https://dap2711.github.io/index/';

function isEmail(s) {
  return typeof s === 'string' && /^\S+@\S+\.\S+$/.test(s.trim());
}

function ownerEmail() {
  return Session.getEffectiveUser().getEmail();
}

// Проверка, что адрес скрипта работает (открыть /exec в браузере)
function doGet() {
  return out('ok');
}

function doPost(e) {
  let d;
  try { d = JSON.parse(e.postData.contents); } catch (x) { return out('bad'); }
  if (d.secret !== SECRET) return out('forbidden');
  const p = PropertiesService.getScriptProperties();
  const key = 'r_' + String(d.id).replace(/[^A-Za-z0-9_-]/g, '');
  if (d.action === 'stats') {
    p.setProperty('s_' + String(d.month).slice(0, 7), JSON.stringify(d.counts || {}));
  } else if (d.action === 'delete') {
    p.deleteProperty(key);
  } else if (d.action === 'upsert') {
    const old = p.getProperty(key);
    const o = old ? JSON.parse(old) : null;
    const rec = {
      title: String(d.title || '').slice(0, 300),
      notes: String(d.notes || '').slice(0, 3000),
      at: d.at,
      to: isEmail(d.me) ? d.me.trim() : '',
      // уже отправленное не шлём повторно, пока время напоминания не изменилось
      sent: !!(o && o.at === d.at && o.sent)
    };
    // старые напоминания (больше суток назад) письмом не отправляем
    if (Date.parse(rec.at) < Date.now() - 24 * 3600 * 1000) rec.sent = true;
    p.setProperty(key, JSON.stringify(rec));
  }
  return out('ok');
}

function out(t) { return ContentService.createTextOutput(t); }

// Вызывается триггером каждые 5 минут
function check() {
  const p = PropertiesService.getScriptProperties();
  const all = p.getProperties();
  const now = Date.now();
  Object.keys(all).forEach(function (k) {
    if (k.indexOf('r_') !== 0) return;
    const r = JSON.parse(all[k]);
    if (r.sent) {
      if (Date.parse(r.at) < now - 7 * 24 * 3600 * 1000) p.deleteProperty(k);
      return;
    }
    if (Date.parse(r.at) <= now) {
      const body = r.title + (r.notes ? '\n\n' + r.notes : '') + '\n\nОткрыть напоминалку: ' + APP_URL;
      MailApp.sendEmail(r.to || ownerEmail(), 'Напоминание: ' + r.title, body);
      r.sent = true;
      p.setProperty(k, JSON.stringify(r));
    }
  });
}

// Письмо со статистикой нажатий за прошлый месяц (триггер 1-го числа)
function monthlyReport() {
  const now = new Date();
  const prev = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  const key = Utilities.formatDate(prev, Session.getScriptTimeZone(), 'yyyy-MM');
  const raw = PropertiesService.getScriptProperties().getProperty('s_' + key);
  if (!raw) return;
  const c = JSON.parse(raw);
  const rows = Object.keys(c).map(function (k) { return [k, c[k]]; })
    .sort(function (a, b) { return b[1] - a[1]; });
  const total = rows.reduce(function (s, r) { return s + r[1]; }, 0);
  const body = 'Статистика нажатий за ' + key + '\nВсего нажатий: ' + total + '\n\n' +
    rows.map(function (r) { return r[1] + '  ' + r[0]; }).join('\n');
  MailApp.sendEmail(ownerEmail(), 'Напоминалка: статистика за ' + key, body);
}

// Проверка почты: запустите вручную, письмо придёт владельцу скрипта
function testMail() {
  MailApp.sendEmail(ownerEmail(), 'Напоминалка: тест', 'Если вы читаете это письмо, отправка работает.');
}

// Запустить один раз вручную: создаёт триггеры
function setup() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('check').timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('monthlyReport').timeBased().onMonthDay(1).atHour(8).create();
}
