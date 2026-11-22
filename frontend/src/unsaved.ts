export interface FormOutlet {
  setUnsaved: (value: boolean) => void
}

export function confirmDiscard() {
  return window.confirm('Есть несохранённые изменения сна. Уйти без сохранения?')
}
