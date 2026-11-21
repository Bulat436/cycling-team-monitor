import { attentionLabels, measure, periodLabel, signedMeasure, statusLabels } from './trainingApi'
import type { Attention, Coverage, DataStatus, LoadWeek, RecoveryPattern } from './trainingApi'

export function DataBadge({ status }: { status: DataStatus }) {
  const tone = status === 'sufficient' ? 'active' : status === 'unsupported' ? 'neutral' : 'pending'
  return <span className={'status training-badge ' + tone}>{statusLabels[status]}</span>
}

export function AttentionBadge({ attention }: { attention: Attention | null }) {
  const tone = attention === 'high' ? 'training-high' : attention === 'moderate' ? 'pending' : 'neutral'
  return <span className={'status training-badge ' + tone}>{attention ? attentionLabels[attention] : 'Нет предупреждений'}</span>
}

export function CoverageDetails({ coverage }: { coverage: Coverage }) {
  return <p className="muted">
    Дней с известным TSS {coverage.known_days} из {coverage.days} · подтверждено {coverage.confirmed_days} из {coverage.days}
    {' · '}тренировок с TSS {coverage.workouts_with_tss} из {coverage.workouts}
  </p>
}

export function recoveryStatus(pattern: RecoveryPattern) {
  if (pattern.sustained === null) return 'Мало оценённых дней'
  return pattern.sustained ? 'Устойчивое ухудшение' : 'Устойчивого ухудшения не выявлено'
}

export function WeekSummary({ week }: { week: LoadWeek }) {
  return <section className="panel">
    <div className="chart-header"><h2>Нагрузка за 7 завершённых дней</h2><DataBadge status={week.status} /></div>
    <p className="muted">{periodLabel(week.period)}</p>
    <dl className="measurements">
      <div><dt>Записанная нагрузка</dt><dd>{measure(week.recorded_tss, 'TSS')}</dd></div>
      <div><dt>Индивидуальный уровень</dt><dd>{measure(week.baseline_tss, 'TSS')}</dd></div>
      <div><dt>Отклонение сопоставимой нагрузки</dt><dd>{signedMeasure(week.delta_pct, '%')}</dd></div>
      <div><dt>Подтверждённый отдых</dt><dd>{week.rest_days} дн.</dd></div>
    </dl>
    {week.comparable_tss != null && week.comparable_tss !== week.recorded_tss &&
      <p>В сравнение вошло {measure(week.comparable_tss, 'TSS')}.</p>}
    <CoverageDetails coverage={week.coverage} />
  </section>
}
