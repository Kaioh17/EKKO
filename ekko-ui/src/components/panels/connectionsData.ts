import {
  Bot, Calendar, CheckSquare, Cloud, Contact, Folder, Globe, HardDrive, Mail, MapPin, MessageSquare,
  Plug, Search, User, Users,
  type LucideIcon,
} from "lucide-react";
import {
  siFitbit, siGithub, siGooglechrome, siGoogle, siGmail, siHomeassistant, siNotion, siObsidian,
  siSpotify, siTodoist, type SimpleIcon,
} from "simple-icons";

// Mock data for the Connections gallery. Nothing here is wired to the backend yet.
// `icon` is a simple-icons brand, a lucide fallback, "microsoft" (4-square mark), or
// { logo } to reuse ProviderLogo (claude / openai / gemini).
export type Icon = SimpleIcon | LucideIcon | "microsoft" | { logo: string };

export type Field =
  | { kind: "text" | "secret"; label: string; value?: string; placeholder?: string; mono?: boolean; hint?: string }
  | { kind: "select"; label: string; options: string[]; value: string; hint?: string }
  | { kind: "toggle"; label: string; on: boolean; hint?: string };

export const GROUPS = ["Google", "Microsoft", "Life & context", "Coding agents", "MCP servers", "Web"] as const;
export type Group = (typeof GROUPS)[number];

export interface Service {
  id: string;
  name: string;
  group: Group;
  icon: Icon;
  blurb: string;
  /** Shown as a "Soon" card: visible in the gallery, not connectable. */
  soon?: boolean;
  /** What this gives ekko, in the user's terms. */
  learns: string[];
  /** Permission toggles (OAuth scopes etc.), all off-by-default except read-only basics. */
  scopes?: { label: string; on: boolean; write?: boolean }[];
  fields?: Field[];
  /** Numbered setup steps, for things the user installs themselves. */
  setup?: string[];
  /** A command to copy, shown in a code block. */
  snippet?: string;
  connect: string;
  docs?: { label: string; href: string };
}

const google = (id: string, name: string, icon: Icon, blurb: string, rest: Partial<Service>): Service => ({
  id, name, icon, blurb, group: "Google", connect: "Sign in with Google", learns: [], ...rest,
});
const ms = (id: string, name: string, icon: Icon, blurb: string, rest: Partial<Service>): Service => ({
  id, name, icon, blurb, group: "Microsoft", connect: "Sign in with Microsoft", learns: [], ...rest,
});

export const SERVICES: Service[] = [
  // ── Google ──────────────────────────────────────────────
  google("google-account", "Google account", siGoogle, "One sign-in that the other Google services share.", {
    learns: ["Your name, email and time zone, so briefings greet and time things correctly", "Which Google services you’ve allowed ekko to use"],
    scopes: [{ label: "Profile and email address", on: true }],
    fields: [{ kind: "select", label: "Account", options: ["Not signed in"], value: "Not signed in", hint: "You can link more than one account later." }],
    docs: { label: "Google OAuth overview", href: "https://developers.google.com/identity/protocols/oauth2" },
  }),
  google("gmail", "Gmail", siGmail, "Let ekko read the inbox and draft replies.", {
    learns: ["Who writes to you, and who you answer fast (a ‘people who matter’ list)", "Bills, deliveries and travel that belong in a briefing", "Threads waiting on your reply"],
    scopes: [
      { label: "Read mail", on: true },
      { label: "Create drafts (never sends)", on: true, write: true },
      { label: "Send mail on your behalf", on: false, write: true },
    ],
    fields: [
      { kind: "select", label: "Look at", options: ["Inbox only", "Inbox + Starred", "All mail"], value: "Inbox only" },
      { kind: "text", label: "Ignore senders", placeholder: "newsletters@, no-reply@", mono: true, hint: "Comma separated. Matching mail is never read." },
      { kind: "toggle", label: "Include mail in the daily briefing", on: true },
    ],
  }),
  google("gcal", "Google Calendar", Calendar, "Know your day, find gaps, add events by voice.", {
    learns: ["What’s on today and tomorrow, so ‘what’s next?’ just works", "Your usual working hours and free blocks", "Recurring habits (gym, standup, school run)"],
    scopes: [
      { label: "Read events", on: true },
      { label: "Create and edit events", on: false, write: true },
    ],
    fields: [
      { kind: "select", label: "Calendars", options: ["All calendars", "Primary only", "Choose…"], value: "Primary only" },
      { kind: "select", label: "Remind me", options: ["Off", "5 minutes before", "10 minutes before", "30 minutes before"], value: "10 minutes before" },
    ],
  }),
  google("gdrive", "Google Drive", HardDrive, "Find and summarise your documents when asked.", {
    learns: ["Titles and recent edits, to answer ‘where’s that doc?’", "Contents of files, only when you ask about them"],
    scopes: [{ label: "Read files", on: true }, { label: "Create and edit files", on: false, write: true }],
    fields: [{ kind: "text", label: "Only these folders", placeholder: "Leave empty for all of Drive", mono: true }],
  }),
  google("gtasks", "Google Tasks", CheckSquare, "To-dos that ekko can read out and tick off.", {
    learns: ["What’s due and overdue", "Which lists matter to you"],
    scopes: [{ label: "Read tasks", on: true }, { label: "Add and complete tasks", on: true, write: true }],
  }),
  google("gcontacts", "Contacts", Contact, "Put names to people, numbers and birthdays.", {
    learns: ["Who ‘Sam’ is when you say ‘email Sam’", "Birthdays and anniversaries for the briefing"],
    scopes: [{ label: "Read contacts", on: true }],
  }),
  google("gmaps", "Maps & location", MapPin, "Commute times and ‘how long to get there?’", {
    soon: true,
    learns: ["Home and work addresses, for commute estimates", "Traffic before an event, so you leave on time"],
  }),

  // ── Microsoft ───────────────────────────────────────────
  ms("ms-account", "Microsoft account", "microsoft", "Work, school or personal. One sign-in for the rest.", {
    learns: ["Your name, email and tenant", "Which Microsoft services you’ve allowed ekko to use"],
    scopes: [{ label: "Profile and email address", on: true }],
    fields: [{ kind: "select", label: "Account type", options: ["Personal", "Work or school"], value: "Personal" }],
    docs: { label: "Microsoft identity platform", href: "https://learn.microsoft.com/entra/identity-platform/" },
  }),
  ms("outlook", "Outlook mail", Mail, "Same as Gmail, for Outlook and Microsoft 365.", {
    learns: ["Who writes to you, and what needs a reply", "Meeting invites waiting for an answer"],
    scopes: [{ label: "Read mail", on: true }, { label: "Create drafts (never sends)", on: true, write: true }, { label: "Send mail on your behalf", on: false, write: true }],
    fields: [{ kind: "select", label: "Look at", options: ["Inbox only", "Focused inbox", "All folders"], value: "Focused inbox" }],
  }),
  ms("outlook-cal", "Outlook calendar", Calendar, "Your schedule from Outlook or Microsoft 365.", {
    learns: ["Today’s meetings and free time", "Teams links, so ‘join my next meeting’ can work"],
    scopes: [{ label: "Read events", on: true }, { label: "Create and edit events", on: false, write: true }],
  }),
  ms("onedrive", "OneDrive", Cloud, "Find files in OneDrive and SharePoint.", {
    learns: ["Recent files and titles", "Contents of files, only when you ask"],
    scopes: [{ label: "Read files", on: true }],
  }),
  ms("todo", "Microsoft To Do", CheckSquare, "Lists and reminders from To Do and Planner.", {
    learns: ["What’s due today", "Which lists you actually use"],
    scopes: [{ label: "Read tasks", on: true }, { label: "Add and complete tasks", on: true, write: true }],
  }),
  ms("teams", "Teams", Users, "Mentions and unread chats in the briefing.", {
    soon: true,
    learns: ["Chats waiting on you", "Meeting recordings and notes (if you allow it)"],
  }),

  // ── Life & context ──────────────────────────────────────
  { id: "home-assistant", name: "Home Assistant", group: "Life & context", icon: siHomeassistant, connect: "Connect",
    blurb: "Lights, sensors and presence, so ekko knows what the house is doing.",
    learns: ["Whether you’re home, and which rooms are in use", "Device states for ‘is the garage open?’"],
    scopes: [{ label: "Read device states", on: true }, { label: "Control devices", on: false, write: true }],
    fields: [
      { kind: "text", label: "Server URL", placeholder: "http://homeassistant.local:8123", mono: true },
      { kind: "secret", label: "Long-lived access token", placeholder: "Paste token", hint: "Stored on this computer only." },
    ],
    docs: { label: "Create a long-lived token", href: "https://www.home-assistant.io/docs/authentication/" } },
  { id: "obsidian", name: "Obsidian", group: "Life & context", icon: siObsidian, connect: "Choose vault",
    blurb: "Your notes become memory ekko can search.",
    learns: ["Projects, ideas and people you write about", "Daily notes, to see what you’ve been focused on"],
    fields: [
      { kind: "text", label: "Vault folder", placeholder: "C:\\Users\\yourname\\Documents\\Vault", mono: true },
      { kind: "toggle", label: "Index on save (watch for changes)", on: true },
      { kind: "text", label: "Skip folders", placeholder: "Private, Journal", mono: true },
    ] },
  { id: "notion", name: "Notion", group: "Life & context", icon: siNotion, connect: "Connect Notion",
    blurb: "Pages and databases you choose to share.", soon: true,
    learns: ["Pages you share with ekko, and nothing else"] },
  { id: "todoist", name: "Todoist", group: "Life & context", icon: siTodoist, connect: "Connect Todoist",
    blurb: "Tasks and projects, read out in the briefing.",
    learns: ["What’s due today and this week", "Your projects and priorities"],
    scopes: [{ label: "Read tasks", on: true }, { label: "Add and complete tasks", on: true, write: true }] },
  { id: "github", name: "GitHub", group: "Life & context", icon: siGithub, connect: "Connect GitHub",
    blurb: "PRs waiting on you, failing CI, issues assigned to you.",
    learns: ["Review requests and mentions", "Which repos you’re working in right now"],
    scopes: [{ label: "Read repos and notifications", on: true }],
    fields: [{ kind: "text", label: "Only these repos", placeholder: "owner/repo, owner/other", mono: true }] },
  { id: "spotify", name: "Spotify", group: "Life & context", icon: siSpotify, connect: "Connect Spotify",
    blurb: "Taste and playback: ‘play something like this’.",
    learns: ["Artists and genres you return to", "What’s playing now"],
    scopes: [{ label: "Read listening history", on: true }, { label: "Control playback", on: true, write: true }] },
  { id: "fitbit", name: "Fitbit", group: "Life & context", icon: siFitbit, connect: "Connect Fitbit",
    blurb: "Sleep and activity, to pace the day.", soon: true,
    learns: ["Last night’s sleep", "Steps and workouts"] },
  { id: "messages", name: "Messages", group: "Life & context", icon: MessageSquare, connect: "Connect",
    blurb: "Texts and chats that need an answer.", soon: true, learns: ["Unread conversations from people on your ‘matter’ list"] },
  { id: "profile", name: "About you", group: "Life & context", icon: User, connect: "Edit",
    blurb: "Facts you tell ekko directly: name, pronouns, work, routines.",
    learns: ["Whatever you type here, nothing is inferred"],
    fields: [
      { kind: "text", label: "What should ekko call you?", placeholder: "Name" },
      { kind: "text", label: "Work hours", placeholder: "09:00–17:30", mono: true },
      { kind: "text", label: "Anything else ekko should always know", placeholder: "I’m vegetarian. I work from home on Fridays." },
    ] },

  // ── Coding agents ───────────────────────────────────────
  { id: "claude-code", name: "Claude Code", group: "Coding agents", icon: { logo: "claude" }, connect: "Detect install",
    blurb: "Hand a build task to Claude Code and get the result back.",
    learns: ["Which project you’re working in, so ‘fix the tests’ lands in the right folder", "What the agent did, for the memory log"],
    fields: [
      { kind: "text", label: "Command", value: "claude", mono: true },
      { kind: "text", label: "Default working folder", placeholder: "C:\\Users\\yourname\\projects", mono: true },
      { kind: "select", label: "Permissions", options: ["Ask before every edit", "Allow edits, ask for commands", "Plan only (read-only)"], value: "Plan only (read-only)" },
      { kind: "toggle", label: "Ekko may start sessions without asking", on: false },
    ],
    setup: ["Install Claude Code and sign in once from a terminal.", "Press Detect install so ekko finds the command.", "Pick a default folder and a permission level."],
    snippet: "claude --version",
    docs: { label: "Claude Code docs", href: "https://docs.claude.com/en/docs/claude-code/overview" } },
  { id: "codex", name: "Codex", group: "Coding agents", icon: { logo: "openai" }, connect: "Detect install",
    blurb: "OpenAI’s coding agent, run from your terminal.",
    learns: ["Same as Claude Code: project, task, outcome"],
    fields: [
      { kind: "text", label: "Command", value: "codex", mono: true },
      { kind: "text", label: "Default working folder", placeholder: "C:\\Users\\yourname\\projects", mono: true },
      { kind: "select", label: "Approval mode", options: ["Suggest", "Auto-edit", "Full auto"], value: "Suggest" },
    ],
    setup: ["Install the Codex CLI and sign in.", "Press Detect install.", "Choose how much it may do unasked."],
    snippet: "codex --version",
    docs: { label: "Codex CLI", href: "https://github.com/openai/codex" } },
  { id: "gemini-cli", name: "Gemini CLI", group: "Coding agents", icon: { logo: "gemini" }, connect: "Detect install",
    blurb: "Google’s open-source terminal agent.", soon: true,
    learns: ["Same as the other agents"] },
  { id: "agent-custom", name: "Other agent", group: "Coding agents", icon: Bot, connect: "Save",
    blurb: "Any CLI that takes a prompt: Aider, Cursor CLI, your own script.",
    learns: ["What it was asked and what came back"],
    fields: [
      { kind: "text", label: "Name", placeholder: "aider" },
      { kind: "text", label: "Command", placeholder: "aider --message {prompt}", mono: true, hint: "{prompt} is replaced with the task." },
      { kind: "text", label: "Working folder", mono: true },
    ] },

  // ── MCP servers ─────────────────────────────────────────
  { id: "mcp-filesystem", name: "Filesystem", group: "MCP servers", icon: Folder, connect: "Add server",
    blurb: "Read and search folders you name. Nothing outside them.",
    learns: ["Files inside the allowed folders only"],
    fields: [
      { kind: "text", label: "Allowed folders", placeholder: "C:\\Users\\yourname\\Documents", mono: true, hint: "One per line." },
      { kind: "toggle", label: "Read-only", on: true },
    ],
    snippet: "npx -y @modelcontextprotocol/server-filesystem C:\\Users\\yourname\\Documents",
    docs: { label: "MCP servers", href: "https://modelcontextprotocol.io/examples" } },
  { id: "mcp-playwright", name: "Playwright", group: "MCP servers", icon: Globe, connect: "Add server",
    blurb: "Drive a real browser: click, fill forms, read pages.",
    learns: ["Pages it visits for you"],
    fields: [{ kind: "toggle", label: "Show the browser window", on: true }],
    snippet: "npx @playwright/mcp@latest",
    docs: { label: "Playwright MCP", href: "https://github.com/microsoft/playwright-mcp" } },
  { id: "mcp-github", name: "GitHub MCP", group: "MCP servers", icon: siGithub, connect: "Add server",
    blurb: "Issues, PRs and code search for the agents ekko runs.", soon: true, learns: ["Repos you grant"] },
  { id: "mcp-custom", name: "Custom server", group: "MCP servers", icon: Plug, connect: "Add server",
    blurb: "Point ekko at any MCP server, local or remote.",
    learns: ["Whatever tools that server exposes"],
    fields: [
      { kind: "select", label: "Transport", options: ["stdio (local command)", "HTTP / SSE (remote)"], value: "stdio (local command)" },
      { kind: "text", label: "Command or URL", placeholder: "npx -y some-mcp-server", mono: true },
      { kind: "text", label: "Environment variables", placeholder: "API_KEY=…", mono: true, hint: "One per line. Stored on this computer only." },
      { kind: "toggle", label: "Start with ekko", on: true },
    ] },

  // ── Web ─────────────────────────────────────────────────
  { id: "browser", name: "Browser scraping", group: "Web", icon: siGooglechrome, connect: "Use this browser",
    blurb: "Answer web questions by reading pages in your own browser.",
    learns: ["Pages it opens to answer a question", "Never your history, passwords or open tabs"],
    fields: [
      { kind: "select", label: "Browser", options: ["Chrome", "Edge", "Firefox", "Brave"], value: "Chrome" },
      { kind: "select", label: "Profile", options: ["Separate ekko profile (recommended)", "My profile (signed in)"], value: "Separate ekko profile (recommended)", hint: "A separate profile keeps your logins out of reach." },
      { kind: "toggle", label: "Run in the background (no window)", on: true },
      { kind: "text", label: "Never visit", placeholder: "bank.example.com, mail.example.com", mono: true },
    ] },
  { id: "search-api", name: "Search API", group: "Web", icon: Search, connect: "Add key",
    blurb: "A search service instead of scraping: faster, cleaner results.", soon: true,
    learns: ["Queries only"],
    fields: [{ kind: "select", label: "Provider", options: ["Brave Search", "Tavily", "SearXNG (self-hosted)"], value: "Brave Search" }] },
];
