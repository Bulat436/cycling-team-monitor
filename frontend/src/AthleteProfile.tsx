import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'

import { dateLabel, numberLabel, recordedAt, useAthleteResource, useSaveAthleteData } from './athleteApi'
import type { AthleteProfile, Zone } from './athleteApi'
import { QueryFeedback, SaveFeedback } from './AthleteFeedback'

const timezoneSuggestions = [...new Set([
  'Etc/UTC', 'Europe/Moscow', ...Intl.supportedValuesOf?.('timeZone') ?? [],
])].sort()

function ProfileForm({ profile, path }: { profile: AthleteProfile; path: string }) {
  const save = useSaveAthleteData()

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const field = (key: string) => String(data.get(key)).trim() || null
    save.mutate({
      path,
      body: {
        name: field('name'),
        birth_date: field('birth_date'),
        height_cm: field('height_cm') === null ? null : Number(field('height_cm')),
        specialization: field('specialization'),
        team_group: field('team_group'),
        timezone: field('timezone'),
        coach_notes: field('coach_notes'),
      },
    })
  }

  return (
    <form className="form-stack" onSubmit={submit}>
      <div className="form-grid">
        <label>Имя и фамилия<input name="name" maxLength={120} required defaultValue={profile.name} /></label>
        <label>Дата рождения<input name="birth_date" type="date" defaultValue={profile.birth_date ?? ''} /></label>
        <label>Рост, см<input name="height_cm" type="number" min="0.1" max="300" step="0.1" defaultValue={profile.height_cm ?? ''} /></label>
        <label>Специализация<input name="specialization" maxLength={120} defaultValue={profile.specialization ?? ''} /></label>
        <label>Команда или группа<input name="team_group" maxLength={120} defaultValue={profile.team_group ?? ''} /></label>
        <label>Часовой пояс (IANA)<input name="timezone" list="timezones" autoComplete="off" required maxLength={64} defaultValue={profile.timezone} /></label>
        <datalist id="timezones">{timezoneSuggestions.map(zone => <option value={zone} key={zone} />)}</datalist>
      </div>
      <p className="muted">Используйте название города или региона из базы IANA, например Europe/Moscow.</p>
      <label>Заметки тренера<textarea name="coach_notes" maxLength={4000} rows={4} defaultValue={profile.coach_notes ?? ''} /></label>
      <div className="actions"><button disabled={save.isPending}>Сохранить профиль</button></div>
      <SaveFeedback error={save.error} saved={save.isSuccess} />
    </form>
  )
}

function MeasurementHistory({ profile, path, edit, ftp }: {
  profile: AthleteProfile; path: string; edit: boolean; ftp: boolean
}) {
  const save = useSaveAthleteData()
  const [date, setDate] = useState('')
  const [value, setValue] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const title = ftp ? 'История FTP' : 'История массы тела'
  const measure = ftp ? 'FTP, Вт' : 'Масса, кг'
  const rows = ftp
    ? profile.ftp_history.map(row => ({ id: null, date: row.effective_from, value: row.watts }))
    : profile.weight_history.map(row => ({ id: row.id, date: row.measured_on, value: row.kg }))

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const measurementDate = date || profile.local_date
    if (ftp) {
      save.mutate({ path: path + '/ftp/' + measurementDate, body: { watts: Number(value) } })
      return
    }
    save.mutate({
      path: path + '/weight' + (editingId === null ? '' : '/' + editingId),
      method: editingId === null ? 'POST' : 'PUT',
      body: { measured_on: measurementDate, kg: Number(value) },
    }, {
      onSuccess: () => {
        setEditingId(null)
        setValue('')
      },
    })
  }

  return (
    <section className="panel">
      <h2>{title}</h2>
      {edit && (
        <form className="form-stack" onSubmit={submit}>
          <div className="measurement-form">
            <label>{ftp ? 'Действует с' : 'Дата измерения'}<input type="date" required value={date || profile.local_date} max={ftp ? undefined : profile.local_date} onChange={event => setDate(event.target.value)} /></label>
            <label>{measure}<input type="number" required min={ftp ? 1 : 0.01} max={ftp ? 3000 : 500} step={ftp ? 1 : 0.01} value={value} onChange={event => setValue(event.target.value)} /></label>
            <div className="actions">
              <button disabled={save.isPending}>{ftp ? 'Сохранить FTP' : editingId === null ? 'Сохранить массу' : 'Сохранить исправление'}</button>
              {editingId !== null && <button type="button" className="secondary" onClick={() => {
                setEditingId(null)
                setDate('')
                setValue('')
                save.reset()
              }}>Отменить</button>}
            </div>
          </div>
          <SaveFeedback error={save.error} saved={save.isSuccess} />
        </form>
      )}
      {rows.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">{ftp ? 'Действует с' : 'Дата измерения'}</th><th scope="col">{measure}</th>{edit && <th scope="col">Действие</th>}</tr></thead>
            <tbody>{rows.map(row => (
              <tr key={row.id ?? row.date}>
                <th scope="row">{dateLabel(row.date)}{ftp && row.date > profile.local_date && <small className="future-mark">Запланировано</small>}</th>
                <td>{numberLabel(row.value)}</td>
                {edit && <td><button type="button" className="secondary" aria-label={'Исправить ' + title.toLowerCase() + ' за ' + dateLabel(row.date)} onClick={() => {
                  setEditingId(row.id)
                  setDate(row.date)
                  save.reset()
                  setValue(String(row.value))
                }}>Исправить</button></td>}
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : <p className="muted">Измерений пока нет.</p>}
    </section>
  )
}

type ZoneDraft = { id: number; name: string; minimum: string; maximum: string }

function ZoneEditor({ initial, path, unit, max }: { initial: Zone[]; path: string; unit: string; max: number }) {
  const nextId = useRef(initial.length)
  const [rows, setRows] = useState<ZoneDraft[]>(() => initial.map((zone, index) => ({
    id: index, name: zone.name, minimum: String(zone.minimum), maximum: zone.maximum === null ? '' : String(zone.maximum),
  })))
  const [error, setError] = useState('')
  const save = useSaveAthleteData()

  function update(id: number, field: 'name' | 'minimum' | 'maximum', value: string) {
    setRows(drafts => drafts.map(row => row.id === id ? { ...row, [field]: value } : row))
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const zones = rows.map(row => ({
      name: row.name.trim(), minimum: Number(row.minimum), maximum: row.maximum === '' ? null : Number(row.maximum),
    }))
    if (zones.some((zone, index) =>
      !zone.name || (zone.maximum !== null && zone.minimum >= zone.maximum) ||
      (index > 0 && (zones[index - 1].maximum === null || zones[index - 1].maximum! > zone.minimum))
    )) {
      setError('Укажите названия и расположите зоны по возрастанию. Границы зон не должны пересекаться.')
      return
    }
    setError('')
    save.mutate({ path, body: { zones } })
  }

  return (
    <form className="form-stack" onSubmit={submit}>
      <p className="muted">Единицы — {unit}. Верхняя граница исключается из диапазона. У последней зоны её можно оставить пустой.</p>
      {rows.map((row, index) => (
        <div key={row.id} className="zone-row" role="group" aria-label={'Зона ' + (index + 1)}>
          <label>Название<input required value={row.name} maxLength={120} onChange={event => update(row.id, 'name', event.target.value)} /></label>
          <label>От<input type="number" required min={0} max={max} step={1} value={row.minimum} onChange={event => update(row.id, 'minimum', event.target.value)} /></label>
          <label>До<input type="number" min={1} max={max} step={1} value={row.maximum} onChange={event => update(row.id, 'maximum', event.target.value)} /></label>
          <button type="button" className="secondary danger" aria-label={'Убрать зону ' + (index + 1)} onClick={() => setRows(drafts => drafts.filter(draft => draft.id !== row.id))}>Убрать</button>
        </div>
      ))}
      <div className="actions">
        <button type="button" className="secondary" disabled={rows.length >= 12} onClick={() => {
          const id = nextId.current++
          setRows(drafts => [...drafts, { id, name: '', minimum: '', maximum: '' }])
        }}>Добавить зону</button>
        <button disabled={save.isPending}>Сохранить зоны</button>
      </div>
      {error ? <p className="error" role="alert">{error}</p> : <SaveFeedback error={save.error} saved={save.isSuccess} />}
    </form>
  )
}

function ZoneSection({ title, zones, edit, path, unit, max }: {
  title: string; zones: Zone[]; edit: boolean; path: string; unit: string; max: number
}) {
  return (
    <section className="panel" aria-label={title}>
      <h2>{title}</h2>
      {edit ? <ZoneEditor initial={zones} path={path} unit={unit} max={max} /> : zones.length ? (
        <dl className="measurements">
          {zones.map((zone, index) => <div key={index}><dt>{zone.name}</dt><dd>{zone.maximum === null ? 'от ' + zone.minimum : 'от ' + zone.minimum + ' до ' + zone.maximum} {unit}</dd></div>)}
        </dl>
      ) : <p className="muted">Зоны не настроены.</p>}
    </section>
  )
}

export function ProfilePage({ athleteId, edit = false }: { athleteId: number; edit?: boolean }) {
  const path = '/athletes/' + athleteId
  const query = useAthleteResource<AthleteProfile>(path)
  const profile = query.data

  return (
    <>
      {edit && <Link className="back-link" to="/">К команде</Link>}
      <header className="page-heading">
        <div><h1>{edit ? 'Профиль спортсмена' : 'Профиль'}</h1>{profile && <p>{profile.name}</p>}</div>
        {edit && profile && <div className="actions">
          <Link className="button secondary" to={path + '/diary'}>Дневник спортсмена</Link>
          <Link className="button secondary" to={path + '/workouts'}>Тренировки спортсмена</Link>
        </div>}
      </header>
      <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
      {profile && (
        <>
          <section className="panel">
            <h2>Данные спортсмена</h2>
            <dl className="measurements">
              <div><dt>ID спортсмена</dt><dd>{profile.athlete_id}</dd></div>
              <div><dt>Электронная почта</dt><dd>{profile.email}</dd></div>
              <div><dt>Статус</dt><dd>{profile.active ? 'Активен' : 'Деактивирован'}</dd></div>
              <div><dt>Масса тела</dt><dd>{profile.current_weight === null ? 'Нет данных' : numberLabel(profile.current_weight.kg) + ' кг'}</dd></div>
              <div><dt>Текущий FTP</dt><dd>{profile.current_ftp === null ? 'Нет данных' : profile.current_ftp.watts + ' Вт'}</dd></div>
              <div><dt>Показатели обновлены</dt><dd>{profile.indicators_updated_at === null ? 'Нет данных' : recordedAt(profile.indicators_updated_at, profile.timezone)}</dd></div>
            </dl>
            {edit ? <ProfileForm profile={profile} path={path} /> : (
              <dl className="measurements">
                <div><dt>Дата рождения</dt><dd>{profile.birth_date ? dateLabel(profile.birth_date) : 'Нет данных'}</dd></div>
                <div><dt>Рост</dt><dd>{profile.height_cm === null ? 'Нет данных' : numberLabel(profile.height_cm) + ' см'}</dd></div>
                <div><dt>Специализация</dt><dd>{profile.specialization ?? 'Нет данных'}</dd></div>
                <div><dt>Команда или группа</dt><dd>{profile.team_group ?? 'Нет данных'}</dd></div>
                <div><dt>Часовой пояс</dt><dd>{profile.timezone}</dd></div>
              </dl>
            )}
          </section>
          <MeasurementHistory profile={profile} path={path} edit={edit} ftp={false} />
          <MeasurementHistory profile={profile} path={path} edit={edit} ftp />
          <ZoneSection title="Зоны мощности" zones={profile.power_zones} edit={edit} path={path + '/zones/power'} unit="Вт" max={5000} />
          <ZoneSection title="Зоны пульса" zones={profile.heart_rate_zones} edit={edit} path={path + '/zones/heart-rate'} unit="уд/мин" max={300} />
          {!edit && <section className="panel"><h2>OnelapFit</h2><p className="muted">Подключение аккаунта станет доступно на этапе интеграции.</p></section>}
        </>
      )}
    </>
  )
}
