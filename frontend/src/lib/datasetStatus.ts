// Статуси версій набору (VersionStatus, рис. 5.8) українською.
export const VERSION_STATUS_UK: Record<string, string> = {
  QUARANTINE: 'карантин — підтвердьте попередження',
  PENDING_REVIEW: 'на перевірці DG-офіцера',
  PUBLISHED: 'опубліковано',
  REJECTED: 'відхилено',
  SUPERSEDED: 'замінено новою версією',
};

// Статуси заявки на закриті дані (AccessRequestStatus) і дозволу (GrantStatus) українською.
export const REQUEST_STATUS_UK: Record<string, string> = {
  SUBMITTED: 'на розгляді адміністратора',
  NEEDS_CLARIFICATION: 'потребує уточнення',
  PROVIDER_REVIEW: 'на розгляді постачальника',
  AWAITING_DATA: 'очікує на дані',
  AWAITING_AGREEMENT: 'очікує прийняття угоди',
  GRANTED: 'дозвіл видано',
  REJECTED: 'відхилено',
  CANCELLED: 'скасовано',
  EXPIRED: 'строк дозволу завершився',
};

export const GRANT_STATUS_UK: Record<string, string> = {
  ACTIVE: 'чинний',
  SUSPENDED: 'призупинено',
  EXPIRED: 'завершився',
};
