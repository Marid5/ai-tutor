import { getProgress, type ForecastDay, type Progress as ProgressData } from '../api';
import { LoadError, Loading } from '../components/Loading';
import { ProgressBar } from '../components/ProgressBar';
import { Stat } from '../components/Stat';
import { count } from '../plural';
import { useLoader } from '../useLoader';

// Learning days are calendar dates in the course's timezone, so format them
// as plain dates (UTC) rather than shifting them into the browser's timezone.
const dayFormat = new Intl.DateTimeFormat('en', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' });

export function formatDay(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? day : dayFormat.format(date);
}

function Forecast({ days }: { days: ForecastDay[] }) {
  if (!days.length) {
    return <p className="muted">No reviews scheduled yet. Finish a lesson to start your schedule.</p>;
  }
  const peak = Math.max(...days.map(item => item.amount));
  return (
    <ul className="forecast">
      {days.map(item => (
        <li key={item.day}>
          <span className="forecast-day">{formatDay(item.day)}</span>
          <ProgressBar value={item.amount} max={peak} size="sm" label={`${formatDay(item.day)}: ${count(item.amount, 'card')}`} />
          <span className="forecast-amount">{item.amount}</span>
        </li>
      ))}
    </ul>
  );
}

function Board({ data }: { data: ProgressData }) {
  return (
    <>
      <div className="stats">
        <Stat value={`${data.cards_ready}/${data.cards_total}`} label="cards ready" />
        <Stat value={data.checks_30d ? `${data.retention_30d}%` : '—'} label="correct checks" />
        <Stat value={`${data.session_minutes} min`} label="studied, 30 days" />
      </div>

      <section className="card">
        <div className="row">
          <span>Lessons completed</span>
          <b>{data.lessons_completed}/{data.lessons_total}</b>
        </div>
        <ProgressBar value={data.lessons_completed} max={data.lessons_total} tone="good" label="Lessons completed" />
        <div className="row row-spaced">
          <span>Checks in the last 30 days</span>
          <b>{data.checks_30d}</b>
        </div>
      </section>

      <h2 className="section-title">Upcoming reviews</h2>
      <section className="card">
        <Forecast days={data.forecast_7d} />
      </section>

      <h2 className="section-title">Hardest cards</h2>
      <section className="card">
        {data.problem_cards.length ? (
          <ul className="problems">
            {data.problem_cards.map(card => (
              <li key={card.id}>
                <div>
                  <p className="problem-prompt">{card.prompt}</p>
                  <p className="muted">{card.answer}</p>
                </div>
                <span className="chip chip-warn">{count(card.again_count, 'miss', 'misses')}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No misses yet.</p>
        )}
      </section>
    </>
  );
}

export function Progress() {
  const [state, reload] = useLoader(getProgress);
  return (
    <div className="progress-screen">
      <h1 tabIndex={-1}>Your progress</h1>
      {state.status === 'loading' && <Loading />}
      {state.status === 'error' && <LoadError error={state.error} onRetry={reload} />}
      {state.status === 'ready' && <Board data={state.data} />}
    </div>
  );
}
