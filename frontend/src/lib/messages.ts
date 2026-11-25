// Коди помилок API → зрозумілий текст (NFR-22: без технічних деталей). EN додамо разом з i18n (Е6).
import { ApiError } from './session';

const UK: Record<string, string> = {
  INVALID_CREDENTIALS: 'Невірний e-mail або пароль.',
  ACCOUNT_LOCKED: 'Забагато невдалих спроб. Вхід тимчасово заблоковано.',
  ACCOUNT_NOT_ACTIVE: 'Обліковий запис ще не активний.',
  RATE_LIMITED: 'Забагато запитів. Спробуйте трохи пізніше.',
  CONSENT_REQUIRED: 'Без згоди на обробку персональних даних реєстрація неможлива.',
  CONSENT_VERSION_OUTDATED: 'Текст згоди оновився. Оновіть сторінку й ознайомтеся з новою версією.',
  CONSENT_UPDATE_REQUIRED: 'Текст згоди оновився — прийміть нову версію в профілі, щоб продовжити роботу.',
  WEAK_PASSWORD: 'Пароль має містити щонайменше 10 символів, літери й цифри.',
  INVALID_EMAIL: 'Перевірте формат e-mail.',
  INVALID_COUNTRY_CODE: 'Оберіть країну зі списку.',
  ORGANIZATION_REQUIRED: 'Оберіть організацію або вкажіть її назву.',
  ORGANIZATION_NOT_FOUND: 'Організацію не знайдено.',
  ORGANIZATION_NOT_VERIFIED: 'Організацію ще не верифіковано.',
  VERIFICATION_DOCUMENT_REQUIRED:
    'Додайте документ, що підтверджує працевлаштування (PDF, PNG або JPEG до 2 МБ), або вкажіть e-mail на домені організації.',
  UNSUPPORTED_FILE_TYPE: 'Підтримуються лише PDF, PNG або JPEG.',
  FILE_TOO_LARGE: 'Файл завеликий.',
  TOKEN_INVALID: 'Посилання недійсне або вже використане.',
  TOKEN_EXPIRED: 'Строк дії посилання минув.',
  PASSWORD_INCORRECT: 'Невірний пароль.',
  PROVIDER_HAS_ACTIVE_GRANTS:
    'На набори вашої організації є чинні дозволи. Видалення можливе після призначення іншого постачальника.',
  DELETION_CONFIRMATION_REQUIRED: 'Підтвердьте видалення даних, отриманих за дозволами.',
  SELF_REVIEW_FORBIDDEN: 'Не можна розглядати власну заявку.',
  INVALID_STATE: 'Дія недоступна в поточному статусі.',
  FORBIDDEN_ROLE: 'Ваша роль не дозволяє цю дію.',
  NOT_AUTHENTICATED: 'Потрібно увійти.',
  SESSION_REVOKED: 'Сесію завершено. Увійдіть знову.',
  SESSION_EXPIRED: 'Сесія завершилася. Увійдіть знову.',
  NOT_FOUND: 'Не знайдено.',
  VALIDATION_ERROR: 'Перевірте заповнення полів.',
  NOTHING_TO_UPDATE: 'Немає змін для збереження.',
  DIRECT_ACCESS_FORBIDDEN: 'Доступ лише через сайт.',
  ACCESS_DENIED: 'Немає доступу до цього набору: потрібен чинний дозвіл.',
  GRANT_SUSPENDED: 'Дозвіл призупинено DG-офіцером.',
  GRANT_EXPIRED: 'Строк дозволу минув.',
  INVALID_SCHEDULE: 'Неправильний розклад (cron) — зміни не збережено.',
  UNSUPPORTED_CSV: 'Файл не схожий на CSV-експорт джерела.',
  INDICATOR_HAS_DATA: 'Для показника вже є дані — його можна лише деактивувати.',
  INVALID_RANGES: 'Діапазони суперечать один одному: типовий має лежати в межах можливого.',
  ALREADY_EXISTS: 'Запис із таким кодом уже є.',
  API_KEY_LIMIT: 'Уже є 3 чинні ключі — спершу відкличте непотрібний.',
  API_KEY_INVALID: 'API-ключ недійсний або відкликаний.',
};

export const STATUS_UK: Record<string, string> = {
  PENDING_EMAIL: 'очікує підтвердження e-mail',
  PENDING_REVIEW: 'очікує розгляду адміністратором',
  ACTIVE: 'активний',
  REJECTED: 'відхилено',
  DEACTIVATED: 'деактивовано',
  ANONYMIZED: 'анонімізовано',
  PENDING: 'на розгляді',
  NEEDS_CLARIFICATION: 'потребує уточнення',
  APPROVED: 'схвалено',
};

export const ROLE_UK: Record<string, string> = {
  ANALYST: 'Аналітик',
  ORG_REPRESENTATIVE: 'Представник організації',
  DATA_PROVIDER: 'Постачальник даних',
  ADMIN: 'Адміністратор',
  DG_OFFICER: 'DG-офіцер',
};

export function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const base = UK[err.code] ?? `Помилка (${err.code}).`;
    if (err.code === 'ACCOUNT_NOT_ACTIVE' && typeof err.body.status === 'string') {
      return `Обліковий запис ${STATUS_UK[err.body.status] ?? err.body.status}.`;
    }
    if (typeof err.body.retry_after === 'number') {
      return `${base} Спробуйте через ${Math.ceil(err.body.retry_after / 60)} хв.`;
    }
    return base;
  }
  return 'Сервер недоступний. Спробуйте ще раз.';
}
