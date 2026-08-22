// App shell: logged out shows the login view, logged in the tabs over chat, records and notes.

import { useState, useSyncExternalStore, type ReactNode } from "react";

import { getSession, subscribe, type Session } from "./auth";
import { AppLayout, Tabs } from "./components/layout";
import type { Tab } from "./components/layout/Tabs";
import { useConversations } from "./lib/conversations";
import { ChatView } from "./views/ChatView";
import { ConversationsSidebar } from "./views/ConversationsSidebar";
import { LoginView } from "./views/LoginView";
import { NotesView } from "./views/NotesView";
import { RecordsView } from "./views/RecordsView";
import { SessionBadge } from "./views/SessionBadge";

const CHAT = "chat";
const RECORDS = "records";
const NOTES = "notes";

const TABS: Tab[] = [
  { id: CHAT, label: "Chat", icon: "message-circle" },
  { id: RECORDS, label: "Records", icon: "users" },
  { id: NOTES, label: "Notes", icon: "file-text" },
];

export default function App() {
  const session = useSyncExternalStore(subscribe, getSession, getSession);

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
 */
function SignedIn({ session }: { session: Session }) {
  const conversations = useConversations();
  const [tab, setTab] = useState(CHAT);
  const [opened, setOpened] = useState<string[]>([CHAT]);

  function select(next: string): void {
    setTab(next);
    setOpened((previous) => (previous.includes(next) ? previous : [...previous, next]));
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
