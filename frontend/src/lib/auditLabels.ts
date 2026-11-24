// Типи подій журналу аудиту (AuditAction, рис. 5.8; перелік подій — DG-17) українською.
export const AUDIT_ACTION_UK: Record<string, string> = {
  LOGIN: 'вхід',
  LOGIN_FAILED: 'невдалий вхід',
  CONSENT_CHANGE: 'згода на обробку ПД',
  REGISTRATION_DECISION: 'рішення щодо реєстрації',
  ROLE_CHANGE: 'зміна ролі',
  REQUEST_SUBMIT: 'подання заявки',
  REQUEST_DECISION: 'рішення щодо заявки',
  AGREEMENT_ACCEPT: 'прийняття угоди',
  GRANT_ISSUE: 'видача дозволу',
  GRANT_SUSPEND: 'призупинення дозволу',
  GRANT_EXPIRE: 'завершення дозволу',
  DATASET_UPLOAD: 'завантаження набору',
  DATASET_PUBLISH: 'публікація / імпорт набору',
  DATASET_REJECT: 'відхилення версії набору',
  DATA_VIEW: 'перегляд даних',
  DATA_DOWNLOAD: 'вивантаження даних',
  CONFIG_CHANGE: 'зміна налаштувань',
  AUDIT_VIEW: 'перегляд журналу аудиту',
  AUDIT_EXPORT: 'експорт журналу аудиту',
  PERSONAL_DATA_EXPORT: 'експорт власних ПД',
  ACCOUNT_DELETE: 'видалення / анонімізація облікового запису',
  RETENTION_PURGE: 'видалення за строком зберігання',
  DELETION_CONFIRM: 'підтвердження видалення даних',
  SECURITY_INCIDENT: 'інцидент безпеки / заборонена дія',
};

export const AUDIT_RESULT_UK: Record<string, string> = {
  SUCCESS: 'успіх',
  DENIED: 'відмова',
  ERROR: 'помилка',
};

export const OBJECT_TYPE_UK: Record<string, string> = {
  user: 'обліковий запис',
  registration: 'заявка на реєстрацію',
  organization: 'організація',
  dataset: 'набір',
  dataset_version: 'версія набору',
  import_run: 'запуск імпорту',
  access_request: 'заявка на дані',
  agreement: 'угода',
  grant: 'дозвіл',
  audit_log: 'журнал аудиту',
  retention: 'строки зберігання',
  endpoint: 'маршрут API',
};
