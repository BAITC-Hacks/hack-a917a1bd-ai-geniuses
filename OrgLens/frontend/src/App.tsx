import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, DragEvent, FormEvent, ReactNode } from 'react';
import type { Analysis, Department, DocumentRecord, EvidenceRef, Health, OrgFunction, SourceBlock, SourceResponse, Version } from './types';

type IconName = 'lens' | 'upload' | 'file' | 'arrow' | 'check' | 'close' | 'external' | 'layers' | 'shield' | 'search' | 'download' | 'spark' | 'alert';
function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    lens: <><circle cx="10" cy="10" r="6" /><path d="m15 15 5 5M10 7v6M7 10h6" /></>,
    upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5" /></>,
    file: <><path d="M14 2H5v20h14V7l-5-5Zm0 0v6h5M8 12h8M8 16h6" /></>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    external: <><path d="M14 3h7v7m0-7L11 13M10 3H3v18h18v-7" /></>,
    layers: <><path d="m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5" /></>,
    shield: <><path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4Z" /><path d="m8 11 3 3 5-6" /></>,
    search: <><circle cx="10" cy="10" r="7" /><path d="m15 15 6 6" /></>,
    download: <><path d="M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5" /></>,
    spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4M18 4h4" /></>,
    alert: <><path d="m12 3 10 18H2L12 3ZM12 9v5M12 17v.1" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

const versionLabels = { before: 'ДО', after: 'ПОСЛЕ' };
const evidenceLabels = { direct: 'Указано в документе', semantic: 'Совпадает по смыслу', insufficient: 'Нужно уточнить' };
const changeLabels = { preserved: 'Сохранено', renamed: 'Переименовано', transformed: 'Преобразовано', split: 'Разделено', merged: 'Объединены', created: 'Создано', abolished: 'Упразднено', uncertain: 'Требует уточнения' };
const relationLabels = { preserved: 'Сохранена', transferred: 'Передана', partially_covered: 'Частично покрыта', not_found: 'Не найдено закрепление', explicitly_discontinued: 'Явно отменена', uncertain: 'Требует уточнения' };
const findingLabels = { potential_loss: 'Потенциальная потеря', potential_duplication: 'Возможное дублирование', partial_overlap: 'Частичное пересечение', potential_conflict: 'Потенциальный конфликт', insufficient_data: 'Недостаточно данных' };
const roleLabels = { execution: 'Исполнение', approval: 'Согласование', control: 'Контроль', independent_audit: 'Независимый аудит', support: 'Поддержка', unknown: 'Роль не определена' };
const documentLabels = { pending: 'В очереди', processed: 'Прочитан', partial: 'Прочитан частично', failed: 'Не прочитан' };
const statusLabels = { queued: 'В очереди', running: 'Анализ выполняется', completed: 'Анализ завершён', partial: 'Частичный результат', failed: 'Анализ не завершён' };
const disclaimer = 'Выводы носят рекомендательный характер и требуют проверки ответственным сотрудником';

function formatSize(size: number) { return size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} КБ` : `${(size / 1024 / 1024).toFixed(1)} МБ`; }
function countLabel(value: number, forms: [string, string, string]) { const last = value % 10; const teen = value % 100; return `${value} ${forms[teen >= 11 && teen <= 14 ? 2 : last === 1 ? 0 : last >= 2 && last <= 4 ? 1 : 2]}`; }
function normalizeWhitespace(text: string) { return text.replace(/[\s\u001c-\u001f]+/gu, ' ').trim(); }
function locationLabel(block: SourceBlock) {
  return [block.page != null && `Страница ${block.page}`, block.section && `Пункт ${block.section}`, block.paragraph_index != null && `Абзац ${block.paragraph_index}`, block.table != null && `Таблица ${block.table}`, block.sheet && `Лист «${block.sheet}»`, block.cell_range && `Ячейки ${block.cell_range}`].filter(Boolean).join(' · ') || 'Извлечённый фрагмент';
}
function readableError(error: unknown) { return error instanceof Error ? error.message : 'Не удалось выполнить запрос. Попробуйте ещё раз.'; }
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try { response = await fetch(url, options); }
  catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    throw new Error('Нет соединения с сервером. Проверьте, что backend запущен на 127.0.0.1:8000.');
  }
  if (!response.ok) {
    let message = `Сервер вернул ошибку ${response.status}. Повторите запрос.`;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') message = body.detail;
      else if (Array.isArray(body.detail)) message = 'Запрос не прошёл проверку. Проверьте файлы и идентификатор анализа.';
    } catch { /* Сервер может вернуть ответ без JSON. */ }
    throw new Error(message);
  }
  try { return await response.json() as T; }
  catch { throw new Error('Сервер вернул некорректный ответ. Проверьте, что backend доступен.'); }
}
function readSavedId() { try { return localStorage.getItem('orglens:last-analysis') || ''; } catch { return ''; } }
function currentId() { return new URLSearchParams(window.location.search).get('analysis_id') || ''; }

function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) { return <span className={`badge badge-${tone}`}>{children}</span>; }
function EmptyState({ title, children }: { title: string; children?: ReactNode }) { return <div className="empty-state"><span className="empty-icon"><Icon name="search" size={26} /></span><h3>{title}</h3><p>{children || 'Попробуйте изменить фильтры.'}</p></div>; }
function Alert({ children, danger = false }: { children: ReactNode; danger?: boolean }) { return <div className={`notice ${danger ? 'notice-danger' : 'notice-warning'}`} role={danger ? 'alert' : 'status'}><Icon name="alert" /><div>{children}</div></div>; }
function evidenceTone(status: string) { return status === 'direct' ? 'green' : status === 'insufficient' ? 'amber' : 'blue'; }
function relationTone(status: string) { return status === 'preserved' ? 'green' : status === 'transferred' ? 'blue' : status === 'not_found' ? 'red' : 'amber'; }

function UploadPanel({ version, files, onFiles, onRemove, disabled, limit }: { version: Version; files: File[]; onFiles: (files: File[]) => void; onRemove: (index: number) => void; disabled: boolean; limit?: number }) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  function handleDrop(event: DragEvent) { event.preventDefault(); setDragging(false); if (!disabled) onFiles(Array.from(event.dataTransfer.files)); }
  function handleChange(event: ChangeEvent<HTMLInputElement>) { onFiles(Array.from(event.target.files || [])); event.target.value = ''; }
  return <section className={`upload-panel ${version}`}>
    <div className="upload-header"><div className="upload-version">{version === 'before' ? '01' : '02'}</div><div><h3>{version === 'before' ? 'Было' : 'Стало'}</h3><p>{version === 'before' ? 'До изменений' : 'После изменений'}</p></div><span className="file-count">{files.length}</span></div>
    <input ref={input} type="file" multiple accept=".docx,.pdf,.xlsx" hidden onChange={handleChange} disabled={disabled} aria-label={`Выбрать документы ${versionLabels[version]}`} />
    <button type="button" className={`dropzone ${dragging ? 'dragging' : ''}`} aria-label={`Добавить документы: ${version === 'before' ? 'Было' : 'Стало'}`} disabled={disabled} onClick={() => input.current?.click()} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={handleDrop}>
      <span className="upload-icon"><Icon name="upload" size={26} /></span><strong>{files.length ? 'Добавить ещё' : 'Выбрать файлы'}</strong><span>или перетащите сюда</span><small>DOCX, PDF с текстом, XLSX{limit ? ` · до ${limit} МБ на файл` : ''}</small>
    </button>
    {files.length > 0 && <ul className="upload-files">{files.map((file, index) => <li key={`${file.name}-${file.lastModified}-${index}`}><span className="file-type">{file.name.split('.').pop()?.toUpperCase()}</span><div><strong title={file.name}>{file.name}</strong><small>{formatSize(file.size)}</small></div><button type="button" className="icon-button" disabled={disabled} onClick={() => onRemove(index)} aria-label={`Удалить ${file.name}`}><Icon name="close" size={16} /></button></li>)}</ul>}
    <div className="upload-foot"><Icon name="file" size={15} />{files.length ? `${countLabel(files.length, ['файл', 'файла', 'файлов'])} · ${formatSize(files.reduce((sum, file) => sum + file.size, 0))}` : 'Положения, приказы, инструкции'}</div>
  </section>;
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState('');
  const [files, setFiles] = useState<Record<Version, File[]>>({ before: [], after: [] });
  const [analysisId, setAnalysisId] = useState(currentId);
  const [reopenId, setReopenId] = useState(readSavedId);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [retry, setRetry] = useState(0);
  const [tab, setTab] = useState('overview');
  const [source, setSource] = useState<EvidenceRef | null>(null);
  const [filterDepartment, setFilterDepartment] = useState('');
  const [filterType, setFilterType] = useState('');
  const [query, setQuery] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    request<Health>('/api/health', { signal: controller.signal }).then(value => { setHealth(value); setHealthError(''); }).catch(err => { if (!controller.signal.aborted) { setHealth(null); setHealthError(readableError(err)); } });
    return () => controller.abort();
  }, [retry, analysisId]);

  useEffect(() => {
    const listener = () => { setAnalysisId(currentId()); setAnalysis(null); setSource(null); setTab('overview'); setFilterDepartment(''); setFilterType(''); setQuery(''); setError(''); setPollError(''); };
    window.addEventListener('popstate', listener);
    return () => window.removeEventListener('popstate', listener);
  }, []);

  useEffect(() => { window.scrollTo(0, 0); }, [tab, analysisId]);

  useEffect(() => {
    if (!analysisId) return;
    const controller = new AbortController();
    let timeout: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const value = await request<Analysis>(`/api/analyses/${encodeURIComponent(analysisId)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setAnalysis(value); setPollError('');
        if (value.status === 'queued' || value.status === 'running') timeout = setTimeout(poll, 1800);
      } catch (err) { if (!controller.signal.aborted) setPollError(readableError(err)); }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timeout); };
  }, [analysisId, retry]);

  function openAnalysis(id: string) {
    const cleanId = id.trim();
    if (!/^[a-zA-Z0-9_-]{6,80}$/.test(cleanId)) { setError('Введите корректный идентификатор анализа: буквы, цифры, дефисы или подчёркивания.'); return; }
    const url = new URL(window.location.href); url.searchParams.set('analysis_id', cleanId); window.history.pushState({}, '', url);
    try { localStorage.setItem('orglens:last-analysis', cleanId); } catch { /* Результат также доступен по URL. */ }
    setReopenId(cleanId); setAnalysisId(cleanId); setAnalysis(null); setError(''); setPollError(''); setSource(null); setTab('overview'); setFilterDepartment(''); setFilterType(''); setQuery(''); setRetry(value => value + 1);
  }
  function startNew() {
    const url = new URL(window.location.href); url.searchParams.delete('analysis_id'); window.history.pushState({}, '', url);
    setAnalysisId(''); setAnalysis(null); setError(''); setPollError(''); setSource(null);
  }
  function addFiles(version: Version, incoming: File[]) {
    const messages: string[] = [];
    const existing = [...files[version]];
    for (const file of incoming) {
      const extension = file.name.split('.').pop()?.toLowerCase();
      if (!['docx', 'pdf', 'xlsx'].includes(extension || '')) { messages.push(`${file.name}: ${extension === 'doc' || extension === 'xls' ? 'сначала конвертируйте файл в DOCX или XLSX' : 'поддерживаются только DOCX, PDF и XLSX'}.`); continue; }
      if (file.size === 0) { messages.push(`${file.name}: файл пуст.`); continue; }
      if (health && file.size > health.limits.max_file_mb * 1024 * 1024) { messages.push(`${file.name}: превышен лимит ${health.limits.max_file_mb} МБ.`); continue; }
      if (existing.some(item => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified)) continue;
      if (health && existing.length >= health.limits.max_files_per_version) { messages.push(`В комплекте ${versionLabels[version]} допускается до ${health.limits.max_files_per_version} файлов.`); break; }
      existing.push(file);
    }
    setFiles(previous => ({ ...previous, [version]: existing })); setError(messages.join(' '));
  }
  async function startAnalysis(demo = false) {
    if (!demo && (!files.before.length || !files.after.length)) { setError('Добавьте хотя бы один документ в каждый комплект: ДО и ПОСЛЕ.'); return; }
    setBusy(true); setError('');
    try {
      let body: FormData | undefined;
      if (!demo) { body = new FormData(); for (const version of ['before', 'after'] as const) for (const file of files[version]) body.append(version, file); }
      const result = await request<{ analysis_id: string }>(demo ? '/api/demo-analysis' : '/api/analyses', { method: 'POST', body });
      openAnalysis(result.analysis_id);
    } catch (err) { setError(readableError(err)); }
    finally { setBusy(false); }
  }

  const result = analysis?.result;
  const departments = useMemo(() => new Map(result?.departments.map(department => [department.id, department]) || []), [result]);
  const functions = useMemo(() => new Map(result?.functions.map(fn => [fn.id, fn]) || []), [result]);
  const documents = useMemo(() => new Map(analysis?.documents.map(document => [document.id, document]) || []), [analysis]);
  const blocks = useMemo(() => new Map(analysis?.documents.flatMap(document => document.blocks.map(block => [block.block_id, block] as const)) || []), [analysis]);
  const running = analysis?.status === 'running' || analysis?.status === 'queued';
  const activeFilters = filterDepartment || filterType || query;
  const matchesText = (text: string) => text.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru'));
  const departmentName = (id: string) => departments.get(id)?.name || 'Подразделение не определено';
  function refs(items: EvidenceRef[]) { return <EvidenceLinks refs={items} blocks={blocks} documents={documents} onOpen={setSource} />; }
  function selectTab(next: string) { setTab(next); setFilterType(''); setFilterDepartment(''); setQuery(''); }
  function departmentNames(ids: string[]) { return ids.length ? ids.map(id => departmentName(id)).join(' · ') : 'Не указано в документах'; }
  const filterOptions = tab === 'departments' ? changeLabels : tab === 'matrix' ? relationLabels : tab === 'risks' ? findingLabels : null;
  const changes = result?.department_changes.filter(change => (!filterType || change.change_type === filterType) && (!filterDepartment || [...change.before_department_ids, ...change.after_department_ids].includes(filterDepartment)) && matchesText(`${departmentNames(change.before_department_ids)} ${departmentNames(change.after_department_ids)} ${change.explanation}`)) || [];
  const mappings = result?.function_mappings.filter(mapping => (!filterType || mapping.relation === filterType) && (!filterDepartment || [mapping.before_function_id, ...mapping.after_function_ids].some(id => functions.get(id)?.department_id === filterDepartment)) && matchesText(`${functions.get(mapping.before_function_id)?.normalized_description} ${mapping.after_function_ids.map(id => functions.get(id)?.normalized_description).join(' ')} ${mapping.explanation}`)) || [];
  const findings = result?.findings.filter(finding => (!filterType || finding.type === filterType) && (!filterDepartment || finding.affected_department_ids.includes(filterDepartment)) && matchesText(`${finding.title} ${finding.explanation} ${departmentNames(finding.affected_department_ids)}`)) || [];

  const navigation: { id: string; label: string; icon: IconName; count?: number }[] = [
    { id: 'overview', label: 'Обзор', icon: 'spark' },
    { id: 'departments', label: 'Подразделения', icon: 'layers', count: result?.department_changes.length },
    { id: 'matrix', label: 'Функции', icon: 'check', count: result?.function_mappings.length },
    { id: 'risks', label: 'Риски', icon: 'shield', count: result?.findings.length },
    { id: 'sources', label: 'Документы', icon: 'file', count: analysis?.documents.length },
    { id: 'conclusion', label: 'Итоги', icon: 'download' },
  ];
  const finished = analysis?.status === 'completed';
  const hasResults = Boolean(result?.department_changes.length || result?.function_mappings.length || result?.findings.length);

  return <div className="app-shell">
    <aside className="app-sidebar">
      <button className="brand" onClick={startNew} aria-label="OrgLens — новый анализ"><span className="brand-mark"><Icon name="lens" size={25} /></span><span>Org<span className="brand-light">Lens</span></span></button>
      <div className="sidebar-caption">Ваше пространство</div>
      <nav className="sidebar-nav" aria-label="Основная навигация">
        <button className={`sidebar-link ${!analysisId ? 'active' : ''}`} onClick={startNew} aria-current={!analysisId ? 'page' : undefined}><Icon name="upload" /><span>Новый анализ</span></button>
        {!analysisId && reopenId && <button className="sidebar-link" onClick={() => openAnalysis(reopenId)}><Icon name="layers" /><span>Последний анализ</span></button>}
        {analysisId && navigation.map(item => <button key={item.id} className={`sidebar-link ${tab === item.id ? 'active' : ''}`} aria-current={tab === item.id ? 'page' : undefined} onClick={() => selectTab(item.id)}><Icon name={item.icon} /><span>{item.label}</span>{item.count != null && <span className="nav-count">{item.count}</span>}</button>)}
      </nav>
      <div className="sidebar-bottom">
        <details className="sidebar-help"><summary><Icon name="spark" size={18} />Как это работает</summary><ol><li>Добавьте документы до и после изменений.</li><li>Запустите сравнение.</li><li>Проверьте выводы по ссылкам на источники.</li></ol></details>
        <div className="sidebar-status"><span className={`connection-dot ${health ? 'online' : ''}`} /><span>{health ? 'Сервер подключён' : 'Нет соединения'}</span></div>
      </div>
    </aside>
    <div className="main-shell">
      <header className="app-header"><div className="header-inner"><div className="header-path">Рабочее пространство<span>/</span><strong>{analysisId ? 'Анализ документов' : 'Новый анализ'}</strong></div><div className="header-meta">{analysisId ? <button className="button button-secondary button-small" onClick={startNew}><Icon name="upload" size={17} />Новый анализ</button> : <Badge>HackAlem AI</Badge>}</div></div></header>
      <main className="workspace">
        <div className="page-heading"><div><div className="eyebrow">{analysisId ? 'ВСЁ ПО ПОЛОЧКАМ' : 'МЕНЬШЕ РУТИНЫ. БОЛЬШЕ ЯСНОСТИ.'}</div><h1>{analysisId ? 'Изменения в организации' : 'Что изменилось?'}</h1><p>{analysisId ? 'От общей картины — к каждому пункту в документах.' : 'Сравните документы. Узнайте, что стало с отделами и их задачами.'}</p></div></div>
        {error && <Alert danger>{error}</Alert>}
        {healthError && <Alert danger><strong>Не удалось подключиться. </strong>{healthError} <button className="text-button" onClick={() => setRetry(value => value + 1)}>Повторить</button></Alert>}
        {!analysisId ? <>
          <div className="start-layout">
            <section className="upload-workspace" aria-labelledby="upload-title">
              <div className="upload-section-heading"><h2 id="upload-title">Добавьте документы</h2><span>Шаг 1 из 2</span></div>
              <div className="upload-grid">{(['before', 'after'] as const).map(version => <UploadPanel key={version} version={version} files={files[version]} onFiles={incoming => addFiles(version, incoming)} onRemove={index => setFiles(previous => ({ ...previous, [version]: previous[version].filter((_, current) => current !== index) }))} disabled={busy} limit={health?.limits.max_file_mb} />)}<div className="between-arrow"><Icon name="arrow" /></div></div>
              <div className="launch-bar"><div><strong>{files.before.length && files.after.length ? 'Всё готово к сравнению' : 'Добавьте файлы в оба блока'}</strong><p>{files.before.length + files.after.length ? `Выбрано файлов: ${files.before.length + files.after.length}` : 'Можно выбрать несколько документов'}</p></div><button className="button button-primary" disabled={busy || !files.before.length || !files.after.length} onClick={() => void startAnalysis()}>{busy ? <span className="spinner" /> : <Icon name="spark" />}{busy ? 'Загружаем…' : 'Сравнить документы'}{!busy && <Icon name="arrow" size={18} />}</button></div>
              <div className="privacy-note"><Icon name="shield" size={17} /><p>Текст документов отправляется в OpenAI для анализа.<br /><span>Загружайте только разрешённые для такой обработки файлы.</span></p></div>
            </section>
            <aside className="welcome-aside" aria-label="О сравнении документов">
              <section className="preview-card"><span className="preview-tag"><Icon name="spark" size={15} />Понятная картина изменений</span><h2>Было сложно.<br />Стало понятно.</h2><div className="preview-diagram" aria-label="Пример: ИТ-центр разделился на два отдела"><div className="preview-node"><Icon name="layers" size={18} />ИТ-центр</div><div className="preview-branches"><div className="preview-node">Разработка</div><div className="preview-node">Поддержка</div></div></div><div className="preview-result"><span><Icon name="check" size={16} /></span><div><strong>Один отдел → два</strong><p>Пример изменения структуры</p></div></div><p>Каждый вывод — со ссылкой на документ.</p></section>
              <section className="demo-card"><span className="demo-icon"><Icon name="file" size={23} /></span><h3>Сначала попробуем?</h3><p>Есть учебный комплект из 6 файлов. Свои документы не нужны.</p><button className="button button-secondary" disabled={busy} onClick={() => void startAnalysis(true)}>Попробовать на примере<Icon name="arrow" size={17} /></button><small>Запустит AI-анализ учебных документов</small></section>
            </aside>
          </div>
          {health && !health.ai_configured && <Alert><strong>Подключите AI, чтобы сравнить документы.</strong> Добавьте API-ключ в настройки сервера и перезапустите его. Без ключа доступно только чтение файлов.</Alert>}
          <details className="reopen-details"><summary><Icon name="layers" size={17} />Открыть сохранённый анализ</summary><form className="reopen-form" onSubmit={(event: FormEvent) => { event.preventDefault(); openAnalysis(reopenId); }}><label htmlFor="reopen">Идентификатор анализа</label><div><input id="reopen" value={reopenId} onChange={event => setReopenId(event.target.value)} placeholder="Вставьте идентификатор" autoComplete="off" /><button className="button button-secondary" type="submit" disabled={!reopenId.trim()}>Открыть<Icon name="arrow" size={16} /></button></div><small>Также можно открыть сохранённую ссылку на результат.</small></form></details>
        </> : <>
          <div className="analysis-meta"><div>{analysis && <Badge tone={finished ? 'green' : analysis.status === 'failed' ? 'red' : 'amber'}>{statusLabels[analysis.status]}</Badge>}{analysis && <span>{new Date(analysis.created_at).toLocaleString('ru-RU', { dateStyle: 'medium', timeStyle: 'short' })}</span>}</div><details className="inline-details"><summary>Номер анализа</summary><code>{analysisId}</code></details></div>
          {pollError && <Alert danger>{pollError} <button className="text-button" onClick={() => setRetry(value => value + 1)}>Повторить</button></Alert>}
          {!analysis && !pollError && <div className="loading-panel" role="status"><span className="spinner" /><h3>Открываем анализ…</h3></div>}
          {analysis && <>
            {(running || analysis.status === 'failed' || (analysis.status === 'partial' && analysis.error)) && <section className={`progress-panel ${running ? '' : 'progress-partial'}`} role="status"><div className="progress-title">{running ? <span className="spinner" /> : <Icon name="alert" />}<div><strong>{running ? analysis.progress.stage : statusLabels[analysis.status]}</strong><p>{running ? 'Сравниваем документы. Результат появится здесь автоматически.' : analysis.error || 'Некоторые данные не удалось проверить. Посмотрите ограничения перед использованием выводов.'}</p></div></div>{running && <div className="progress-counters"><span><strong>{analysis.progress.processed} / {analysis.progress.total}</strong> на текущем этапе</span></div>}{running && analysis.progress.total > 0 && <progress value={analysis.progress.processed} max={analysis.progress.total} aria-label="Обработано на текущем этапе" />}</section>}
            {analysis.warnings.length > 0 && <details className="warnings-panel"><summary><Icon name="alert" size={17} />Что учесть при чтении результата · {analysis.warnings.length}</summary><ul>{analysis.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details>}
            <section className="results-panel">
              <div className="results-toolbar"><nav className="tabs" aria-label="Разделы анализа">{navigation.map(item => <button key={item.id} className={tab === item.id ? 'active' : ''} aria-current={tab === item.id ? 'page' : undefined} onClick={() => selectTab(item.id)}>{item.label}{item.count != null && <span>{item.count}</span>}</button>)}</nav></div>
              {filterOptions && <div className="filters"><label className="search-field"><Icon name="search" size={17} /><input aria-label="Поиск по результатам" placeholder="Найти в результатах" value={query} onChange={event => setQuery(event.target.value)} /></label><select aria-label="Фильтр по подразделению" value={filterDepartment} onChange={event => setFilterDepartment(event.target.value)}><option value="">Все подразделения</option>{result?.departments.map(department => <option value={department.id} key={department.id}>{department.name} · {versionLabels[department.version]}</option>)}</select><select aria-label="Фильтр по типу результата" value={filterType} onChange={event => setFilterType(event.target.value)}><option value="">Все изменения</option>{Object.entries(filterOptions).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>{activeFilters && <button className="text-button" onClick={() => { setFilterDepartment(''); setFilterType(''); setQuery(''); }}>Сбросить</button>}</div>}
              <div id={`panel-${tab}`}>
                {tab === 'overview' && <div className="result-content">
                  <div className="result-overview"><span className="overview-symbol"><Icon name={running ? 'search' : hasResults ? 'spark' : 'file'} size={30} /></span><div className="overview-copy"><span className="section-kicker">Коротко о главном</span><h2>{running ? 'Собираем общую картину' : hasResults ? 'Вот что удалось выяснить' : finished ? 'Сравнение завершено' : 'Документы здесь. Выводы ещё не готовы.'}</h2><p>{running ? 'Можно перейти к документам, пока идёт анализ.' : hasResults ? 'Начните с рисков или посмотрите, как изменились задачи отделов.' : finished ? 'Откройте итоги, чтобы увидеть результат и ограничения проверки.' : 'Откройте файлы и извлечённый текст. Для выводов нужен завершённый AI-анализ.'}</p><div className="overview-actions"><button className="button button-primary" onClick={() => selectTab(hasResults ? 'risks' : 'sources')}>{hasResults ? 'Посмотреть риски' : 'Открыть документы'}<Icon name="arrow" size={17} /></button>{hasResults && <button className="text-button" onClick={() => selectTab('conclusion')}>Читать итоги</button>}</div></div></div>
                  <div className="stat-grid"><Stat label="Документы" value={analysis.documents.length} detail={`Прочитано полностью: ${analysis.documents.filter(document => document.status === 'processed').length} из ${analysis.documents.length}`} icon="file" /><Stat label="Подразделения" value={result?.departments.length || 0} detail={`${result?.departments.filter(department => department.version === 'before').length || 0} до · ${result?.departments.filter(department => department.version === 'after').length || 0} после`} icon="layers" /><Stat label="Найдено соответствий" value={result?.function_mappings.filter(mapping => mapping.after_function_ids.length > 0 && ['preserved', 'transferred', 'partially_covered'].includes(mapping.relation)).length || 0} detail={`из ${result?.functions.filter(fn => departments.get(fn.department_id)?.version === 'before').length || 0} функций до изменений`} icon="check" /><Stat label="Требуют внимания" value={result?.findings.length || 0} detail="риски и вопросы к данным" icon="alert" warning={Boolean(result?.findings.length)} /></div>
                  <div className="section-heading"><h2>Что проверить</h2><button className="text-button" onClick={() => selectTab('risks')}>Все риски<Icon name="arrow" size={16} /></button></div>
                  <div className="risk-summary" aria-label="Количество выводов по категориям">{Object.entries(findingLabels).map(([type, label]) => { const count = result?.findings.filter(finding => finding.type === type).length || 0; if (!count && (type === 'partial_overlap' || type === 'insufficient_data')) return null; return <button key={type} className={`risk-summary-item ${count ? 'has-findings' : ''}`} onClick={() => { selectTab('risks'); setFilterType(type); }}><span>{label}</span><strong>{count}</strong></button>; })}</div>
                  {!result?.findings.length && <p className="muted">{finished ? 'Проверенные риски не выявлены в пределах этого анализа.' : 'Пока нет проверенных выводов. Это не означает, что рисков нет.'}</p>}
                </div>}
                {tab === 'departments' && <div className="result-content"><div className="section-heading"><div><h2>Как изменилась структура</h2><p>Какие отделы сохранились, объединились или появились.</p></div><span>{countLabel(changes.length, ['результат', 'результата', 'результатов'])}</span></div>{changes.length ? <div className="change-list">{changes.map((change, index) => <article className="change-card" key={index}><div className="card-top"><Badge tone={change.change_type === 'preserved' ? 'green' : change.change_type === 'uncertain' ? 'amber' : 'blue'}>{changeLabels[change.change_type]}</Badge><Badge tone={evidenceTone(change.evidence_status)}>{evidenceLabels[change.evidence_status]}</Badge></div><div className="department-flow"><div><small>БЫЛО</small><strong>{change.change_type === 'created' && !change.before_department_ids.length ? 'Не было' : departmentNames(change.before_department_ids)}</strong></div><span className="flow-arrow"><Icon name="arrow" /></span><div><small>СТАЛО</small><strong>{change.change_type === 'abolished' && !change.after_department_ids.length ? 'Упразднено' : departmentNames(change.after_department_ids)}</strong></div></div><p>{change.explanation}</p><details className="reading-details"><summary>Посмотреть источники · {change.source_refs.length}</summary>{refs(change.source_refs)}</details></article>)}</div> : <EmptyState title={activeFilters ? 'Ничего не нашлось' : 'Изменений пока нет'}>{activeFilters ? 'Попробуйте другие фильтры или запрос.' : 'Они появятся, когда AI сравнит оба комплекта документов.'}</EmptyState>}{!activeFilters && Boolean(result?.departments.length) && <details className="department-inventory"><summary>Все подразделения · {result?.departments.length}</summary><div className="inventory-grid">{result?.departments.map(department => <div key={department.id}><Badge>{versionLabels[department.version]}</Badge><strong>{department.name}</strong>{department.parent_name && <small>В составе: {department.parent_name}</small>}{refs(department.source_refs)}</div>)}</div></details>}</div>}
                {tab === 'matrix' && <div className="result-content"><div className="section-heading"><div><h2>Что стало с задачами</h2><p>Сравнение функций до и после изменений.</p></div><span>{countLabel(mappings.length, ['результат', 'результата', 'результатов'])}</span></div>{mappings.length ? <div className="matrix-list">{mappings.map((mapping, index) => <article className="mapping-card" key={`${mapping.before_function_id}-${index}`}><div className="mapping-head"><Badge tone={relationTone(mapping.relation)}>{relationLabels[mapping.relation]}</Badge></div><div className="mapping-columns"><div className="mapping-column"><span className="detail-label">Было</span><FunctionCell fn={functions.get(mapping.before_function_id)} departments={departments} /></div><span className="flow-arrow"><Icon name="arrow" /></span><div className="mapping-column"><span className="detail-label">Стало</span>{mapping.after_function_ids.length ? mapping.after_function_ids.map(id => <FunctionCell key={id} fn={functions.get(id)} departments={departments} />) : <p className="muted">Подтверждённое соответствие не найдено</p>}</div></div><p className="mapping-explanation">{mapping.explanation}</p>{Boolean(mapping.uncovered_aspects.length) && <div className="aspect uncovered"><strong>Не покрыто:</strong> {mapping.uncovered_aspects.join('; ')}</div>}{Boolean(mapping.limitations?.length) && <details className="reading-details limitation"><summary>Есть ограничения · {mapping.limitations.length}</summary><ul>{mapping.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul></details>}<details className="reading-details"><summary>Подробности и источники</summary>{Boolean(mapping.covered_aspects.length) && <div className="aspect"><strong>Покрыто:</strong> {mapping.covered_aspects.join('; ')}</div>}{Boolean(mapping.searched_document_ids?.length) && <details className="inline-details"><summary>Проверенные документы после изменений · {mapping.searched_document_ids.length}</summary><ul>{mapping.searched_document_ids.map(id => <li key={id}>{documents.get(id)?.original_name || 'Документ не найден'}</li>)}</ul></details>}{Boolean(mapping.nearest_after_function_ids?.length) && <details className="inline-details"><summary>Ближайшие соответствия</summary><ul>{mapping.nearest_after_function_ids.map(id => <li key={id}>{functions.get(id)?.normalized_description || 'Функция недоступна'}</li>)}</ul></details>}{refs(mapping.source_refs)}</details></article>)}</div> : <EmptyState title={activeFilters ? 'Ничего не нашлось' : 'Функции ещё не сопоставлены'}>{activeFilters ? 'Попробуйте другие фильтры.' : 'Соответствия появятся после AI-анализа обоих комплектов.'}</EmptyState>}</div>}
                {tab === 'risks' && <div className="result-content"><div className="section-heading"><div><h2>На что обратить внимание</h2><p>Возможные риски, которые стоит проверить по документам.</p></div><span>{countLabel(findings.length, ['результат', 'результата', 'результатов'])}</span></div>{findings.length ? <div className="risk-list">{findings.map(finding => <article className={`risk-card risk-${finding.type}`} key={finding.id} id={`finding-${finding.id}`}><div className="card-top"><Badge tone={finding.type === 'potential_loss' || finding.type === 'potential_conflict' ? 'red' : 'amber'}>{findingLabels[finding.type]}</Badge><Badge tone={evidenceTone(finding.evidence_status)}>{evidenceLabels[finding.evidence_status]}</Badge></div><h3>{finding.title}</h3><div className="risk-departments"><Icon name="layers" size={16} />{departmentNames(finding.affected_department_ids)}</div><p>{finding.explanation}</p><div className="recommendation"><Icon name="check" size={18} /><p><strong>Что сделать</strong>{finding.recommendation || 'Попросите ответственного сотрудника проверить этот пункт.'}</p></div>{finding.limitations.length > 0 && <details className="reading-details limitation"><summary>Есть ограничения · {finding.limitations.length}</summary><ul>{finding.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul></details>}<details className="reading-details"><summary>Почему такой вывод · {countLabel(finding.evidence_refs.length, ['источник', 'источника', 'источников'])}</summary><div className="risk-quotes">{finding.evidence_refs.slice(0, 3).map((ref, index) => <button className="quote-link" key={`${ref.block_id}-${index}`} onClick={() => setSource(ref)}><span>«{ref.quote}»</span><Icon name="external" size={15} /></button>)}</div>{refs(finding.evidence_refs)}<div className="risk-history">До изменений: {finding.existed_before === 'yes' ? 'такая ситуация уже была' : finding.existed_before === 'no' ? 'такая ситуация не выявлена' : 'не удалось установить'}</div></details></article>)}</div> : <EmptyState title={activeFilters ? 'По этим фильтрам ничего нет' : finished ? 'Потенциальные риски не выявлены' : 'Выводы о рисках ещё не готовы'}>{finished ? 'Результат относится к загруженным документам с учётом ограничений анализа.' : 'Для выводов нужен завершённый анализ. Отсутствие карточек пока не означает отсутствие рисков.'}</EmptyState>}</div>}
                {tab === 'sources' && <div className="result-content"><div className="section-heading"><div><h2>Ваши документы</h2><p>Откройте файл, чтобы посмотреть текст и источники выводов.</p></div><span>{countLabel(blocks.size, ['фрагмент', 'фрагмента', 'фрагментов'])}</span></div>{analysis.documents.map(document => <DocumentCard key={document.id} document={document} analysisId={analysisId} onOpen={setSource} />)}{!analysis.documents.length && <EmptyState title="Документы ещё загружаются">Они появятся здесь после сохранения.</EmptyState>}</div>}
                {tab === 'conclusion' && <div className="result-content conclusion"><div className="section-heading"><div><h2>Итоги анализа</h2><p>Главные выводы и их основания.</p></div><div className="export-actions"><a className="button button-primary button-small" href={`/api/analyses/${encodeURIComponent(analysisId)}/report`} target="_blank" rel="noreferrer"><Icon name="download" size={16} />Скачать отчёт</a><a className="button button-quiet button-small" href={`/api/analyses/${encodeURIComponent(analysisId)}/export`} download>JSON</a></div></div><div className="coverage-box"><Icon name="file" /><div><strong>Прочитано полностью: {analysis.documents.filter(document => document.status === 'processed').length} из {analysis.documents.length} документов</strong><p>{analysis.complete ? 'Доступный комплект обработан полностью.' : 'Анализ неполный. Учитывайте предупреждения и ограничения.'}</p><details className="inline-details"><summary>Подробнее об обработке</summary><p>Частично прочитано: {analysis.documents.filter(document => document.status === 'partial').length}. Не прочитано: {analysis.documents.filter(document => document.status === 'failed').length}.</p><ul>{analysis.documents.map(document => <li key={document.id}><Badge>{versionLabels[document.version]}</Badge> {document.original_name} — {documentLabels[document.status]}</li>)}</ul></details></div></div>{result?.conclusion.length ? <div className="conclusion-items">{result.conclusion.map((item, index) => <article key={index}><span className="conclusion-number">{String(index + 1).padStart(2, '0')}</span><div><p>{item.text}</p><details className="reading-details"><summary>Источники вывода</summary>{refs(item.evidence_refs)}</details>{Boolean(item.finding_ids.length) && <div className="linked-findings">{item.finding_ids.map(id => { const finding = result.findings.find(value => value.id === id); return finding ? <button key={id} className="text-button" onClick={() => { selectTab('risks'); setQuery(finding.title); }}>{finding.title}<Icon name="arrow" size={14} /></button> : null; })}</div>}</div></article>)}</div> : <EmptyState title="Итоги ещё не готовы">Они появятся после проверки источников и завершения анализа.</EmptyState>}{Boolean(result?.limitations.length) && <section className="limitations-box"><h3><Icon name="alert" size={18} />Что важно учесть</h3><ul>{result?.limitations.map((limitation, index) => <li key={index}>{limitation}</li>)}</ul></section>}<details className="analysis-tech"><summary>Сведения об анализе</summary><p>Модель: {analysis.model} · Версия инструкции: {analysis.prompt_version} · Вызовов AI: {analysis.progress.api_calls}</p></details></div>}
              </div>
            </section>
            <div className="disclaimer"><Icon name="shield" size={18} /><span>{disclaimer}.</span></div>
          </>}
        </>}
        <footer className="app-footer"><span>OrgLens <span className="footer-dot">·</span> HackAlem AI</span><span>Ясность в каждом изменении</span></footer>
      </main>
    </div>
    {source && analysisId && <SourceDrawer source={source} analysisId={analysisId} onClose={() => setSource(null)} />}
  </div>;
}


function Stat({ label, value, detail, icon, warning = false }: { label: string; value: number; detail: string; icon: IconName; warning?: boolean }) { return <div className={`stat-card ${warning ? 'stat-warning' : ''}`}><div><span>{label}</span><Icon name={icon} size={18} /></div><strong>{value}</strong><small>{detail}</small></div>; }

function EvidenceLinks({ refs, blocks, documents, onOpen }: { refs: EvidenceRef[]; blocks: Map<string, SourceBlock>; documents: Map<string, DocumentRecord>; onOpen: (ref: EvidenceRef) => void }) {
  if (!refs.length) return <span className="no-evidence">Подтверждающий источник не указан</span>;
  return <div className="evidence-links">{refs.map((ref, index) => { const block = blocks.get(ref.block_id); const document = block ? documents.get(block.document_id) : undefined; return <button className="evidence-link" key={`${ref.block_id}-${index}`} onClick={() => onOpen(ref)} title={document ? `${document.original_name} · ${block ? locationLabel(block) : ''}` : 'Открыть источник'}><Icon name="file" size={13} /><span>{document ? `${versionLabels[document.version]} · ${document.original_name}` : `Источник ${index + 1}`}</span>{block?.page != null && <small>с. {block.page}</small>}<Icon name="external" size={12} /></button>; })}</div>;
}

function FunctionCell({ fn, departments }: { fn?: OrgFunction; departments: Map<string, Department> }) {
  if (!fn) return <span className="muted">Функция недоступна</span>;
  return <div className="function-cell"><strong>{fn.normalized_description}</strong><span className="function-department">{departments.get(fn.department_id)?.name || 'Подразделение не определено'}</span><Badge>{roleLabels[fn.role]}</Badge><details className="inline-details"><summary>Исходная формулировка</summary><p>{fn.original_text}</p>{fn.scope && <p><b>Область:</b> {fn.scope}</p>}<p><strong>Действие:</strong> {fn.action}<br /><strong>Объект:</strong> {fn.object}</p></details></div>;
}

function DocumentCard({ document, analysisId, onOpen }: { document: DocumentRecord; analysisId: string; onOpen: (ref: EvidenceRef) => void }) {
  const [search, setSearch] = useState('');
  const [limit, setLimit] = useState(15);
  const filtered = document.blocks.filter(block => `${block.text} ${block.context} ${locationLabel(block)}`.toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru')));
  return <details className="document-card"><summary><span className="file-type">{document.format.replace('.', '').toUpperCase()}</span><div className="document-name"><strong>{document.original_name}</strong><small>{formatSize(document.size)} · {document.blocks.length} фрагментов</small></div><Badge tone={document.version === 'before' ? 'neutral' : 'blue'}>{versionLabels[document.version]}</Badge><Badge tone={document.status === 'processed' ? 'green' : document.status === 'failed' ? 'red' : 'amber'}>{documentLabels[document.status]}</Badge><span className="expand-symbol">+</span></summary><div className="document-body"><div className="document-actions"><a className="button button-secondary button-small" href={`/api/analyses/${encodeURIComponent(analysisId)}/documents/${encodeURIComponent(document.id)}`} target="_blank" rel="noreferrer"><Icon name="download" size={15} />Оригинал документа</a><small className="document-hash" title={document.sha256}>SHA-256: {document.sha256.slice(0, 16)}…</small></div>{document.warnings.length > 0 && <ul className="document-warnings">{document.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}{document.blocks.length > 0 && <label className="search-field block-search"><Icon name="search" size={16} /><input aria-label={`Поиск во фрагментах ${document.original_name}`} placeholder="Поиск по тексту и адресу фрагмента" value={search} onChange={event => { setSearch(event.target.value); setLimit(15); }} /></label>}<div className="source-blocks">{filtered.slice(0, limit).map(block => <button className="source-block" key={block.block_id} onClick={() => onOpen({ block_id: block.block_id, quote: '' })}><span><strong>{locationLabel(block)}</strong><Icon name="external" size={14} /></span><p>{block.text}</p></button>)}</div>{filtered.length > limit && <button className="text-button show-more" onClick={() => setLimit(value => value + 25)}>Показать ещё ({filtered.length - limit})</button>}{!filtered.length && <p className="muted">{document.blocks.length ? 'Совпадения не найдены.' : 'Доступных текстовых фрагментов нет. Проверьте предупреждения документа.'}</p>}</div></details>;
}

function SourceDrawer({ source, analysisId, onClose }: { source: EvidenceRef; analysisId: string; onClose: () => void }) {
  const [data, setData] = useState<SourceResponse | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const closeButton = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    const controller = new AbortController(); setData(null); setError('');
    request<SourceResponse>(`/api/analyses/${encodeURIComponent(analysisId)}/sources/${encodeURIComponent(source.block_id)}`, { signal: controller.signal }).then(setData).catch(err => { if (!controller.signal.aborted) setError(readableError(err)); });
    return () => controller.abort();
  }, [analysisId, source.block_id, retry]);
  const closeRef = useRef(onClose); closeRef.current = onClose;
  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden'; closeButton.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeRef.current();
      if (event.key === 'Tab') {
        const elements = panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input, [tabindex="0"]');
        if (!elements?.length) return;
        const first = elements[0]; const last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => { document.body.style.overflow = previousOverflow; document.removeEventListener('keydown', handleKey); previousFocus?.focus(); };
  }, []);
  const original = data ? `/api/analyses/${encodeURIComponent(analysisId)}/documents/${encodeURIComponent(data.document.id)}` : '';
  const quoteIndex = data && source.quote ? data.block.text.indexOf(source.quote) : -1;
  const quoteMatches = Boolean(data && normalizeWhitespace(source.quote) && normalizeWhitespace(data.block.text).includes(normalizeWhitespace(source.quote)));
  return <div className="drawer-backdrop" onClick={onClose}><aside className="source-drawer" role="dialog" aria-modal="true" aria-labelledby="source-title" onClick={event => event.stopPropagation()} ref={panel}><header className="drawer-header"><div><div className="eyebrow">ИСТОЧНИК</div><h2 id="source-title">Посмотрим в документ</h2></div><button className="icon-button" ref={closeButton} aria-label="Закрыть источник" onClick={onClose}><Icon name="close" /></button></header><div className="drawer-content">{error && <Alert danger><strong>Источник недоступен. </strong>{error} <button className="text-button" onClick={() => setRetry(value => value + 1)}>Повторить</button></Alert>}{!data && !error && <div className="loading-panel"><span className="spinner" /><p>Загружаем источник…</p></div>}{data && <><div className="source-document"><span className="file-type">{data.document.format.replace('.', '').toUpperCase()}</span><div><strong>{data.document.original_name}</strong><p><Badge tone={data.document.version === 'before' ? 'neutral' : 'blue'}>Комплект {versionLabels[data.document.version]}</Badge></p></div></div><div className="source-address"><Icon name="layers" size={17} /><span>{locationLabel(data.block)}</span></div>{source.quote && <section className="quote-section"><h3>Точная цитата</h3><blockquote>{source.quote}</blockquote>{!quoteMatches && <p className="limitation">Цитата не найдена в тексте блока даже с учётом различий в пробелах и переносах строк. Сверьте её с оригиналом и контекстом.</p>}</section>}<section className="context-section"><h3>{source.quote ? 'Фрагмент с окружающим текстом' : 'Извлечённый текст'}</h3><div className="source-text">{quoteIndex >= 0 ? <>{data.block.text.slice(0, quoteIndex)}<mark>{source.quote}</mark>{data.block.text.slice(quoteIndex + source.quote.length)}</> : data.block.text}</div>{data.block.context && data.block.context !== data.block.text && <><h3>Контекст раздела или таблицы</h3><div className="source-context">{data.block.context}</div></>}</section>{data.document.warnings.length > 0 && <Alert><strong>Ограничения документа</strong><ul>{data.document.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></Alert>}<a className="button button-primary" href={`${original}${data.block.page && data.document.format.toLowerCase().replace('.', '') === 'pdf' ? `#page=${data.block.page}` : ''}`} target="_blank" rel="noreferrer"><Icon name="external" size={17} />{data.document.format.toLowerCase().replace('.', '') === 'pdf' ? 'Открыть оригинал PDF' : 'Скачать оригинал'}</a><details className="source-block-id"><summary>Идентификатор фрагмента</summary><code>{data.block.block_id}</code></details></>}</div><footer className="drawer-footer"><Icon name="shield" size={16} />Адрес и текст взяты из загруженного документа.</footer></aside></div>;
}
