import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { numberLabel } from './athleteApi'

const blue = '#175fbc'
const teal = '#08776f'
const grid = '#dce6ef'
const axis = '#445a72'
const tooltipValue = (value: unknown) => typeof value === 'number' ? numberLabel(value) : 'Нет данных'

export interface TrendPoint { x: number; value: number | null; baseline?: number | null }

export function TrendChart({ title, data, unit, ticks, step = false, baseline = false, domain }: {
  title: string; data: TrendPoint[]; unit: string; ticks: (value: number) => string; step?: boolean;
  baseline?: boolean; domain?: [number, number]
}) {
  if (!data.some(point => point.value !== null || point.baseline != null)) return <p className="muted">Нет данных за выбранный период.</p>
  return (
    <div className="chart-frame" role="group" aria-label={title}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 12, right: 16, left: 0, bottom: 6 }}>
          <CartesianGrid stroke={grid} vertical={false} />
          <XAxis dataKey="x" type="number" domain={['dataMin', 'dataMax']} tickFormatter={ticks} minTickGap={28}
            tick={{ fill: axis, fontSize: 13 }} axisLine={{ stroke: grid }} tickLine={false} />
          <YAxis domain={domain} width={60} unit={unit ? ' ' + unit : ''} tick={{ fill: axis, fontSize: 13 }} tickLine={false} axisLine={false} />
          <Tooltip labelFormatter={value => ticks(Number(value))} formatter={tooltipValue} />
          {baseline && <Legend />}
          <Line dataKey="value" name={baseline ? 'Дневник' : title} stroke={blue} strokeWidth={2}
            type={step ? 'stepAfter' : 'linear'} connectNulls={false}
            dot={data.length < 400 ? { r: 2.5 } : false} isAnimationActive={false} />
          {baseline && <Line dataKey="baseline" name="Среднее за предыдущие 28 дней" stroke={teal} strokeDasharray="5 5"
            strokeWidth={2} connectNulls={false} dot={false} isAnimationActive={false} />}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export function DistributionChart({ title, data, unit, horizontal = false }: {
  title: string; data: { name: string; value: number | null }[]; unit: string; horizontal?: boolean
}) {
  if (!data.some(point => point.value !== null)) return <p className="muted">Нет данных за выбранный период.</p>
  return (
    <div className={'chart-frame' + (horizontal ? ' horizontal-chart' : '')} role="group" aria-label={title}>
      <ResponsiveContainer width="100%" height={horizontal ? Math.max(210, data.length * 33 + 24) : '100%'}>
        <BarChart data={data} layout={horizontal ? 'vertical' : 'horizontal'} margin={{ top: 8, right: 20, left: 0, bottom: 4 }}>
          <CartesianGrid stroke={grid} horizontal={!horizontal} vertical={horizontal} />
          <XAxis dataKey={horizontal ? undefined : 'name'} type={horizontal ? 'number' : 'category'}
            unit={horizontal ? ' ' + unit : undefined} tick={{ fill: axis, fontSize: 13 }} tickLine={false} minTickGap={22} />
          <YAxis dataKey={horizontal ? 'name' : undefined} type={horizontal ? 'category' : 'number'}
            unit={horizontal ? undefined : ' ' + unit} width={horizontal ? 140 : 55} tick={{ fill: axis, fontSize: 13 }} tickLine={false} />
          <Tooltip formatter={tooltipValue} />
          <Bar dataKey="value" name={title} fill={horizontal ? teal : blue} radius={3} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
