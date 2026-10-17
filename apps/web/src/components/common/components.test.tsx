import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { Confirm } from "./confirm"
import { EmptyState } from "./primitives"
import { StatusBadge } from "./status-badge"

describe("StatusBadge", () => {
  it("shows a readable label, never color alone", () => {
    render(<StatusBadge status="DEAD_LETTERED" />)
    const badge = screen.getByText("Dead-lettered")
    expect(badge).toHaveAttribute("data-status", "DEAD_LETTERED")
  })

  it("falls back to the raw status for unknown values", () => {
    render(<StatusBadge status="SOMETHING_NEW" />)
    expect(screen.getByText("SOMETHING_NEW")).toBeInTheDocument()
  })

  it("pulses only for live states unless told not to", () => {
    const { container, rerender } = render(<StatusBadge status="RUNNING" />)
    expect(container.querySelector(".animate-ping")).not.toBeNull()
    rerender(<StatusBadge status="ACTIVE" animate={false} />)
    expect(container.querySelector(".animate-ping")).toBeNull()
  })
})

describe("Confirm", () => {
  it("runs the action only after confirmation", async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    render(
      <Confirm
        trigger={<button type="button">Revoke</button>}
        title="Revoke ci?"
        description="Requests using this key are rejected."
        confirmLabel="Revoke key"
        dismissLabel="Keep it"
        destructive
        onConfirm={onConfirm}
      />,
    )
    await user.click(screen.getByRole("button", { name: "Revoke" }))
    expect(await screen.findByRole("alertdialog")).toHaveTextContent("Requests using this key are rejected.")

    await user.click(screen.getByRole("button", { name: "Keep it" }))
    expect(onConfirm).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: "Revoke" }))
    await user.click(await screen.findByRole("button", { name: "Revoke key" }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })
})

describe("EmptyState", () => {
  it("renders the title, description and action", () => {
    render(<EmptyState title="No tasks yet" description="Submit one." action={<button type="button">Submit</button>} />)
    expect(screen.getByText("No tasks yet")).toBeInTheDocument()
    expect(screen.getByText("Submit one.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Submit" })).toBeInTheDocument()
  })
})
