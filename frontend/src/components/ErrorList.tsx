export function ErrorList({ messages }: { messages: string[] }) {
  if (messages.length === 0) return null
  return (
    <ul role="alert" style={{ color: '#b91c1c', paddingLeft: 20 }}>
      {messages.map((m, i) => (
        <li key={i}>{m}</li>
      ))}
    </ul>
  )
}
