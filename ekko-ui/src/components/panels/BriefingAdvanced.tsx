import "./Panels.css";
import "./BriefingPanel.css";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, BookOpen, Cpu, MicVocal, Newspaper, Sparkles, TrendingUp } from "lucide-react";
import { getProviders, type BriefingSettings, type ProviderInfo } from "../../api/client";
import { useSettingsForm } from "../../hooks/useSettingsForm";
import { DraftInput } from "./SettingsControls";
import { Row, SettingsPage, SettingsSection, Switch } from "./SettingsKit";
import { Area, lines, namesToText, Num, textToNames, type AdvTab, type BriefingFocus } from "./BriefingPanel";

const TABS: { id: AdvTab; label: string; Icon: typeof Cpu }[] = [
  { id: "health", label: "Computer health", Icon: Cpu },
  { id: "news", label: "Tech news", Icon: Newspaper },
  { id: "watchlist", label: "Watchlist", Icon: TrendingUp },
  { id: "narration", label: "AI narration", Icon: Sparkles },
  { id: "wording", label: "Spoken wording", Icon: MicVocal },
];

// Settings > Briefing: the fine print. Everyday choices (what's included, topics, tickers) live on the Briefing page.
function BriefingAdvanced({ focus, onBasic, onModels }: { focus?: BriefingFocus; onBasic: () => void; onModels: () => void }) {
  const form = useSettingsForm<BriefingSettings>("briefing");
  const [tab, setTab] = useState<AdvTab>(focus?.tab ?? "health");
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    getProviders().then(setProviders).catch(() => setProviders([]));
  }, []);

  // A deep link picks the tab, then (once the settings have loaded) scrolls to the row and flashes it.
  useEffect(() => focus && setTab(focus.tab), [focus]);
  const ready = !!form.draft;
  useEffect(() => {
    if (!focus?.row || !ready) return;
    const el = root.current?.querySelector<HTMLElement>(`[data-row="${focus.row}"]`);
    if (!el) return;
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "center" });
    el.classList.remove("is-flash");
    void el.offsetWidth; // restart the animation on a repeat click
    el.classList.add("is-flash");
    const done = () => el.classList.remove("is-flash");
    el.addEventListener("animationend", done, { once: true });
    return () => el.removeEventListener("animationend", done);
  }, [focus, ready, tab]);

  const s = form.draft;
  if (form.loading || !s) {
    return (
      <div className="panel panel--wide">
        <p className="panel__empty">{form.loading ? "Loading…" : (form.error ?? "Settings unavailable.")}</p>
      </div>
    );
  }
  const on = (id: string) => s.sections.find((x) => x.id === id)?.enabled;
  const phrase = (key: keyof BriefingSettings, title: string, hint: string) => (
    <Row title={title} hint={hint}>
      {(id) => <DraftInput aria-labelledby={id} type="text" value={String(s[key])} onCommit={(raw) => raw.trim() && form.set({ [key]: raw.trim() } as Partial<BriefingSettings>)} />}
    </Row>
  );

  return (
    <SettingsPage forms={[form]} note="Saving applies to the next briefing." className="brief">
      <div ref={root} className="bfa">
        <button type="button" className="bfa__back" onClick={onBasic}>
          <ArrowLeft size={14} /> <BookOpen size={14} /> Back to your Briefing
        </button>
        <div className="bfa__tabs" role="tablist">
          {TABS.map(({ id, label, Icon }) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? "is-on" : ""} onClick={() => setTab(id)}>
              <Icon size={15} /> {label}
            </button>
          ))}
        </div>

        {tab === "health" && (
          <SettingsSection title="Computer health limits" hint="Flagged in the health report, and in “system diagnosis”. Which metrics to include is on the Briefing page.">
            <Row anchor="limits" title="CPU load warning (%)" hint="Flagged above this.">{() => <Num value={s.cpu_high_percent} min={1} max={100} onCommit={(n) => form.set({ cpu_high_percent: n })} />}</Row>
            <Row title="Memory warning (%)" hint="Flagged above this.">{() => <Num value={s.ram_high_percent} min={1} max={100} onCommit={(n) => form.set({ ram_high_percent: n })} />}</Row>
            <Row title="Low disk space (% free)" hint="Flagged below this.">{() => <Num value={s.disk_free_low_percent} min={1} max={100} onCommit={(n) => form.set({ disk_free_low_percent: n })} />}</Row>
            <Row title="Low battery (%)" hint="Flagged below this, when unplugged.">{() => <Num value={s.battery_low_percent} min={1} max={100} onCommit={(n) => form.set({ battery_low_percent: n })} />}</Row>
          </SettingsSection>
        )}

        {tab === "news" && (
          <SettingsSection title="Tech news" hint={on("news") ? "Stories come from Hacker News. Topics are on the Briefing page." : "This section is off, so these settings aren't used."}>
            <Row anchor="window" title="How far back (hours)" hint="Only stories newer than this.">{() => <Num value={s.news_window_hours} min={1} max={168} onCommit={(n) => form.set({ news_window_hours: n })} />}</Row>
            <Row title="Stories listed" hint="The most points first. A quiet day may show fewer.">{() => <Num value={s.max_news_items} min={1} max={50} onCommit={(n) => form.set({ max_news_items: n })} />}</Row>
            <Row title="Stories read aloud" hint="0 reads none. These are also the ones AI narration rewrites.">{() => <Num value={s.audible_news_count} min={0} max={10} onCommit={(n) => form.set({ audible_news_count: n })} />}</Row>
            <Row title="Stories in chat" hint="How many headlines the chat version lists.">{() => <Num value={s.chat_news_items} min={1} max={50} onCommit={(n) => form.set({ chat_news_items: n })} />}</Row>
            <Row title="Give up on a topic after (seconds)">{() => <Num int={false} value={s.news_timeout_s} min={1} max={60} onCommit={(n) => form.set({ news_timeout_s: n })} />}</Row>
            <Row title="Open browser tabs" hint="Opens in Brave when the news section runs." toggle>
              {(id) => <Switch labelId={id} checked={s.open_tabs} onChange={(open_tabs) => form.set({ open_tabs })} />}
            </Row>
            <Row title="Top stories to open" hint="How many of the top stories get their own tab.">{() => <Num value={s.tab_news_count} min={0} max={10} onCommit={(n) => form.set({ tab_news_count: n })} />}</Row>
            <Row title="Pages to always open" hint="One address per line, starting with http:// or https://.">
              {() => <Area value={s.extra_tabs.join("\n")} onCommit={(raw) => form.set({ extra_tabs: lines(raw) })} placeholder="https://finance.yahoo.com/quote/SPY/" />}
            </Row>
          </SettingsSection>
        )}

        {tab === "watchlist" && (
          <SettingsSection title="Watchlist" hint={on("stocks") ? "Prices come from Yahoo Finance. Tickers are on the Briefing page." : "This section is off, so these settings aren't used."}>
            <Row anchor="instructions" title="Reason instructions" hint="How the “why it moved” note for each ticker is written. It shows on screen and is only spoken if you ask. Used when narration is AI.">
              {() => <Area rows={4} value={s.stocks_prompt} onCommit={(raw) => raw.trim() && form.set({ stocks_prompt: raw.trim() })} />}
            </Row>
            <Row title="Search the web for reasons" hint="Reasons use Yahoo's free headlines. Turn on to also search for tickers with none. This spends your search credits (Brave or Tavily)." toggle>
              {(id) => <Switch labelId={id} checked={s.reasons_use_search} onChange={(reasons_use_search) => form.set({ reasons_use_search })} />}
            </Row>
            <Row title="Spoken names" hint="For symbols that sound odd aloud. One per line, like “GC=F = gold”.">
              {() => <Area value={namesToText(s.ticker_names)} onCommit={(raw) => form.set({ ticker_names: textToNames(raw) })} placeholder="GC=F = gold" />}
            </Row>
            <Row title="Headlines per ticker" hint="What AI narration bases its take on. 0 skips the lookup.">{() => <Num value={s.stock_headlines} min={0} max={10} onCommit={(n) => form.set({ stock_headlines: n })} />}</Row>
            <Row title="Give up on a ticker after (seconds)">{() => <Num int={false} value={s.stock_timeout_s} min={1} max={60} onCommit={(n) => form.set({ stock_timeout_s: n })} />}</Row>
          </SettingsSection>
        )}

        {tab === "narration" && (
          <SettingsSection title="AI narration" hint="Only used when narration is set to AI on the Briefing page.">
            <Row anchor="provider" title="Provider" hint="Automatic uses your usual provider, falling back down your list if it fails." note={<button type="button" className="panel__link" onClick={onModels}>Models and spending limits <ArrowRight size={11} /></button>}>
              {(id) => (
                <select className="panel__input" aria-labelledby={id} value={s.llm_provider ?? ""} onChange={(e) => form.set({ llm_provider: e.target.value || null })}>
                  <option value="">Automatic</option>
                  {providers.map((p) => (
                    <option key={p.name} value={p.name}>{p.label}</option>
                  ))}
                </select>
              )}
            </Row>
            <Row title="Narration length" hint="How long the spoken briefing runs: short is about 60 words, regular about 150, long about 300.">
              {(id) => (
                <select className="panel__input" aria-labelledby={id} value={s.narration_length} onChange={(e) => form.set({ narration_length: e.target.value as typeof s.narration_length })}>
                  <option value="short">Short</option>
                  <option value="regular">Regular</option>
                  <option value="long">Long</option>
                </select>
              )}
            </Row>
            <Row title="Give up after (seconds)">{() => <Num int={false} value={s.llm_timeout_s} min={1} max={120} onCommit={(n) => form.set({ llm_timeout_s: n })} />}</Row>
            <Row title="Briefing narrator" hint="Who ekko is when it reads your briefing aloud. Length, data and reply format are handled for you.">
              {() => <Area rows={8} value={s.briefing_system_prompt} onCommit={(raw) => raw.trim() && form.set({ briefing_system_prompt: raw.trim() })} />}
            </Row>
            <Row title="News instructions" hint="Chat and the preview: how each story is put in one line.">
              {() => <Area rows={5} value={s.news_prompt} onCommit={(raw) => raw.trim() && form.set({ news_prompt: raw.trim() })} />}
            </Row>
            <Row title="Old briefing window" hint="Falls back to the earlier voice briefing: one AI call and one spoken block per section, and no follow-up question." toggle>
              {(id) => <Switch labelId={id} checked={s.legacy_window} onChange={(legacy_window) => form.set({ legacy_window })} />}
            </Row>
          </SettingsSection>
        )}

        {tab === "wording" && (
          <SettingsSection title="What ekko says" hint="The fixed phrases around the facts.">
            {phrase("speak_system_ok", "Health is fine", "Said when nothing is flagged.")}
            {phrase("speak_system_alert", "Health warning opener", "Starts the line listing what's flagged.")}
            {phrase("speak_handoff", "After the health line", "Said before the news and stocks start.")}
            {phrase("speak_news_single", "One news story", "Starts the line when only one story is read.")}
            {phrase("speak_news_intro", "Several news stories", "Starts the line when more than one is read.")}
            {phrase("speak_stocks_single", "One ticker", "Starts the line when there's only one.")}
            {phrase("speak_stocks_intro", "Several tickers", "Starts the line when there are more.")}
            {phrase("speak_joiner", "Between items", "Said between stories, and between tickers.")}
            {phrase("speak_watchlist_followup", "Watchlist follow-up", "Asked after the briefing; “yes” explains why the watchlist moved.")}
          </SettingsSection>
        )}
      </div>
    </SettingsPage>
  );
}

export default BriefingAdvanced;
