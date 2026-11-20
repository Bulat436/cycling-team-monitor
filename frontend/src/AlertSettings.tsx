import type { FormEvent } from 'react'
import { Link } from 'react-router'

import { QueryFeedback, SaveFeedback } from './AthleteFeedback'
import { useAthleteResource, useSaveAthleteData } from './athleteApi'
import { alertLabels } from './coachApi'
import type { AlertCode, AlertSettings } from './coachApi'

const controls = [
  { key: 'diary_due_hour', label: 'Проверять дневник после местного часа', min: 0, max: 23 },
  { key: 'fatigue_min', label: 'Усталость от', min: 1, max: 5 },
  { key: 'soreness_min', label: 'Мышечная болезненность от', min: 1, max: 5 },
  { key: 'motivation_max', label: 'Мотивация до', min: 1, max: 5 },
  { key: 'rpe_grace_hours', label: 'Ожидание RPE после тренировки, часы', min: 0, max: 168 },
  { key: 'rpe_lookback_days', label: 'Проверять пропуски RPE за, дней', min: 7, max: 90 },
  { key: 'inactive_days', label: 'Отсутствие тренировок, дней', min: 1, max: 90 },
] as const

function SettingsForm({ initial }: { initial: AlertSettings }) {
  const save = useSaveAthleteData()
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const fields = new FormData(event.currentTarget)
    save.mutate({
      path: '/analytics/alert-settings',
      body: {
        ...Object.fromEntries(controls.map(control => [control.key, Number(fields.get(control.key))])),
        enabled: fields.getAll('enabled'),
      },
    })
  }

  return <form className="form-stack" onSubmit={submit}>
    <fieldset className="score-field">
      <legend>Правила</legend>
      <div className="form-grid">
        {Object.entries(alertLabels).map(([code, label]) => (
          <label className="fit-checkbox" key={code}>
            <input type="checkbox" name="enabled" value={code} defaultChecked={initial.enabled.includes(code as AlertCode)} />
            {label}
          </label>
        ))}
      </div>
    </fieldset>
    <div className="form-grid">
      {controls.map(control => <label key={control.key}>{control.label}<input name={control.key} type="number"
        min={control.min} max={control.max} required defaultValue={initial[control.key]} /></label>)}
    </div>
    <div className="actions"><button disabled={save.isPending}>Сохранить настройки</button></div>
    <SaveFeedback error={save.error} saved={save.isSuccess} />
  </form>
}

export function AlertSettingsPage() {
  const query = useAthleteResource<AlertSettings>('/analytics/alert-settings')
  return <>
    <Link className="back-link" to="/">К команде</Link>
    <header className="page-heading"><div><h1>Настройки предупреждений</h1><p>Общие правила мониторинга команды</p></div></header>
    <section className="panel">
      <p className="muted">Предупреждения помогают заметить изменения в данных. Они не являются медицинскими заключениями.</p>
      <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
      {query.data && <SettingsForm initial={query.data} />}
    </section>
  </>
}
