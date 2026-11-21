export function ErrorList({ messages }: { messages: string[] }) {
  if (messages.length === 0) return null
  return (
    <ul role="alert" className="error-list">
      {messages.map((m, i) => (
        <li key={i}>{m}</li>
      ))}
    </ul>
  )
}
