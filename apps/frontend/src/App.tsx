// App shell: logged out shows the login view, logged in the tabs over chat, records, notes, audit.

import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";

import { getSession, subscribe, type Session } from "./auth";
import { AppLayout, Tabs } from "./components/layout";
import type { Tab } from "./components/layout/Tabs";
import { useConversations } from "./lib/conversations";
import { clearLocation, pushLocation, replaceLocation, useLocation } from "./lib/location";
import { AuditView } from "./views/AuditView";
import { ChatView } from "./views/ChatView";
import { ConversationsSidebar } from "./views/ConversationsSidebar";
import { LoginView } from "./views/LoginView";
import { NotesView } from "./views/NotesView";
import { RecordsView } from "./views/RecordsView";
import { SessionBadge } from "./views/SessionBadge";

const CHAT = "chat";
const RECORDS = "records";
const NOTES = "notes";
const AUDIT = "audit";

const TABS: Tab[] = [
  { id: CHAT, label: "Chat", icon: "message-circle" },
  { id: RECORDS, label: "Records", icon: "users" },
  { id: NOTES, label: "Notes", icon: "file-text" },
  // `activity` is the monitoring glyph and is already in the self-hosted subset (Icon.tsx).
  { id: AUDIT, label: "Audit", icon: "activity" },
];

export default function App() {
  const session = useSyncExternalStore(subscribe, getSession, getSession);

  // Signed out - by logout or by a 401 - there is no location to be at, and none to show.
  useEffect(() => {
    if (!session) clearLocation();
  }, [session]);

  if (!session) {
    return (
      <AppLayout>
        <LoginView />
      </AppLayout>
    );
  }

  // Keyed on the identity: a re-login as another tenant mounts a fresh conversation store.
  return <SignedIn key={`${session.tenantId}/${session.username}`} session={session} />;
}

/**
 * The signed-in shell. Every tab a reader has opened stays mounted and is hidden rather than
 * unmounted, because a switch must not cost them a streamed transcript, a filter they typed or
 * a search they ran; a tab they have never opened is not mounted at all, so nothing fetches
 * rows for a tab nobody asked for. The conversation rail belongs to the chat, so it is passed
 * to the shell only while the chat is the open tab.
 *
 * Where the reader is comes from the URL (`lib/location.ts`, issue #135), so a reload lands
 * back there. The shell is the one that knows which tabs exist: a hash naming one that does not
 * is the chat, and a thread id means something only under it. Two directions have to be kept
 * agreeing, and each has exactly one owner here:
 *
 * - the URL moved (a reload, back, forward): `select` on the conversation store opens the thread
 *   the hash names, through the very path a click in the rail takes, so what renders is what
 *   clicking renders. A thread the registry will not hand over - deleted, or another identity's,
 *   which are indistinguishable by design (ADR 0012) - leaves the store on the draft with its
 *   own "could not open" message, and the hash is cleaned to `#/chat`. No retry.
 * - the store moved on its own (the rail opened a thread, New chat, the draft a first question
 *   registered): the URL follows it. `shown` is what the URL is already known to say, so a move
 *   the URL made in the first place is never echoed back as a second one, and the store's own
 *   `chatKey` - bumped on a thread switch and not on anything else - is what decides whether the
 *   move was somewhere to come back to. A switch is pushed; a tab change and a draft becoming a
 *   thread are restated in place, because neither is a new place.
 */
function SignedIn({ session }: { session: Session }) {
  const conversations = useConversations();
  const location = useLocation();
  const known = TABS.some((entry) => entry.id === location.tab);
  const tab = known && location.tab ? location.tab : CHAT;
  const wanted = known && tab === CHAT ? location.threadId : null;
  const [opened, setOpened] = useState<string[]>([tab]);
  const shown = useRef({ threadId: conversations.activeId, chatKey: conversations.chatKey });

  useEffect(() => {
    setOpened((previous) => (previous.includes(tab) ? previous : [...previous, tab]));
  }, [tab]);

  // A hash the shell is not showing is restated in place, so the URL never says something else.
  useEffect(() => {
    if (location.tab !== tab || location.threadId !== wanted) replaceLocation(tab, wanted);
  }, [tab, wanted, location.tab, location.threadId]);

  // Keyed on the hash alone: this effect answers navigation, never the store's own moves.
  useEffect(() => {
    if (tab !== CHAT) return;
    if (!wanted) {
      if (conversations.activeId) conversations.newChat();
      return;
    }
    let live = true;
    void conversations.select(wanted).then((openedId) => {
      if (live && !openedId) replaceLocation(CHAT, null);
    });
    return () => {
      live = false;
    };
  }, [tab, wanted]);

  // The other direction: the store moved on its own and the URL has to follow it.
  useEffect(() => {
    if (tab !== CHAT) return;
    const previous = shown.current;
    const still =
      conversations.activeId === previous.threadId && conversations.chatKey === previous.chatKey;
    if (still) return;
    shown.current = { threadId: conversations.activeId, chatKey: conversations.chatKey };
    if (conversations.activeId === location.threadId) return;
    // A switch is somewhere to come back to; a draft that just became a thread is not.
    const move = conversations.chatKey === previous.chatKey ? replaceLocation : pushLocation;
    move(CHAT, conversations.activeId);
  }, [tab, conversations.activeId, conversations.chatKey, location.threadId]);

  function select(next: string): void {
    replaceLocation(next, next === CHAT ? conversations.activeId : null);
  }

  return (
    <AppLayout
      tabs={<Tabs tabs={TABS} active={tab} onSelect={select} />}
      tenantBadge={<SessionBadge session={session} />}
      sidebar={tab === CHAT ? <ConversationsSidebar store={conversations} /> : undefined}
    >
      <TabPanel id={CHAT} active={tab} opened={opened}>
        <ChatView
          threadId={conversations.activeId}
          replay={conversations.replay}
          chatKey={conversations.chatKey}
          onStart={conversations.startThread}
          onTitled={conversations.titleThread}
        />
      </TabPanel>
      <TabPanel id={RECORDS} active={tab} opened={opened}>
        <RecordsView tenant={session.tenantId} />
      </TabPanel>
      <TabPanel id={NOTES} active={tab} opened={opened}>
        <NotesView tenant={session.tenantId} />
      </TabPanel>
      <TabPanel id={AUDIT} active={tab} opened={opened}>
        <AuditView />
      </TabPanel>
    </AppLayout>
  );
}

function TabPanel({
  id,
  active,
  opened,
  children,
}: {
  id: string;
  active: string;
  opened: string[];
  children: ReactNode;
}) {
  if (!opened.includes(id)) return null;
  return (
    <div className="tab-panel" role="tabpanel" aria-label={id} hidden={id !== active}>
      {children}
    </div>
  );
}
