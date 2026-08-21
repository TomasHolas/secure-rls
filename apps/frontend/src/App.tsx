// App shell: logged out shows the login view, logged in the conversation rail beside the chat.

import { useSyncExternalStore } from "react";

import { getSession, subscribe, type Session } from "./auth";
import { AppLayout } from "./components/layout";
import { useConversations } from "./lib/conversations";
import { ChatView } from "./views/ChatView";
import { ConversationsSidebar } from "./views/ConversationsSidebar";
import { LoginView } from "./views/LoginView";
import { SessionBadge } from "./views/SessionBadge";

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

function SignedIn({ session }: { session: Session }) {
  const conversations = useConversations();

  return (
    <AppLayout
      tenantBadge={<SessionBadge session={session} />}
      sidebar={<ConversationsSidebar store={conversations} />}
    >
      <ChatView
        threadId={conversations.activeId}
        replay={conversations.replay}
        chatKey={conversations.chatKey}
        onStart={conversations.startThread}
      />
    </AppLayout>
  );
}
