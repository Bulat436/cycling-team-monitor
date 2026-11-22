import { sleepRecordLabel, sleepSources } from './sleepApi'
import type { SleepDay } from './sleepApi'
import { measure, signedMeasure } from './trainingApi'
import type { RecoveryValue } from './trainingApi'

export function SleepSummary({ day, comparison, windowDays }: {
  day: SleepDay; comparison?: RecoveryValue; windowDays?: number
}) {
  const comparable = comparison?.value === day.duration_minutes
  return <dl className="measurements sleep-summary">
    <div><dt>Сон в аналитике</dt><dd>{sleepRecordLabel(day)}</dd>
      {day.source && <small>{sleepSources[day.source]}</small>}
      {day.manually_corrected && <small>Ручная поправка</small>}
    </div>
    <div><dt>Утренняя анкета</dt><dd>{day.has_diary ? 'Заполнена' : 'Не заполнена'}</dd></div>
    <div><dt>Индивидуальная норма{windowDays != null ? ` · ${windowDays} дн.` : ''}</dt>
      <dd>{comparison?.baseline != null ? measure(comparison.baseline, 'мин') : 'Пока недоступна'}</dd>
      {comparable && comparison?.delta != null && <small>Отклонение {signedMeasure(comparison.delta, 'мин')}</small>}
    </div>
  </dl>
}
