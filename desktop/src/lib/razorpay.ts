import type { Checkout } from '@shared/lib/types'

interface RazorpayResponse {
  razorpay_order_id: string
  razorpay_payment_id: string
  razorpay_signature: string
}

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => { open: () => void }
  }
}

const SDK = 'https://checkout.razorpay.com/v1/checkout.js'

function loadSdk(): Promise<NonNullable<Window['Razorpay']>> {
  if (window.Razorpay) return Promise.resolve(window.Razorpay)
  return new Promise((resolve, reject) => {
    const el = document.createElement('script')
    el.src = SDK
    el.onload = () => (window.Razorpay ? resolve(window.Razorpay) : reject(new Error('sdk missing')))
    el.onerror = () => reject(new Error('Could not load the payment window. Check your connection.'))
    document.head.appendChild(el)
  })
}

export type CheckoutOutcome =
  | { kind: 'paid'; response: RazorpayResponse }
  | { kind: 'dismissed' }
  | { kind: 'failed'; message: string }

/**
 * Opens Razorpay Checkout and resolves with whatever the studio did.
 *
 * Note what this deliberately does not do: decide anything. A `paid` outcome
 * only means checkout reported success on this machine. The event is activated
 * by the `payment.captured` webhook, and the caller polls for that. A studio who
 * closes the window mid-payment still gets their event.
 */
export function openCheckout(checkout: Checkout, studioName: string): Promise<CheckoutOutcome> {
  // Dev and any mock backend: skip the real SDK entirely.
  if (checkout.key_id.startsWith('rzp_test_mock')) {
    return new Promise((resolve) => {
      const ok = window.confirm(
        `Mock payment\n\n${studioName}\n₹${(checkout.amount_paise / 100).toLocaleString('en-IN')}\n\nOK to pay, Cancel to dismiss.`,
      )
      setTimeout(
        () =>
          resolve(
            ok
              ? {
                  kind: 'paid',
                  response: {
                    razorpay_order_id: checkout.razorpay_order_id,
                    razorpay_payment_id: `pay_mock_${Date.now()}`,
                    razorpay_signature: 'mock_signature',
                  },
                }
              : { kind: 'dismissed' },
          ),
        400,
      )
    })
  }

  return loadSdk().then(
    (Razorpay) =>
      new Promise<CheckoutOutcome>((resolve) => {
        let settled = false
        const done = (o: CheckoutOutcome) => {
          if (!settled) {
            settled = true
            resolve(o)
          }
        }

        const rz = new Razorpay({
          key: checkout.key_id,
          order_id: checkout.razorpay_order_id,
          amount: checkout.amount_paise,
          currency: checkout.currency,
          name: 'Frame',
          description: 'Event',
          // UPI carries no MDR in India, so it costs the platform nothing.
          // Putting it first is worth real money at volume.
          config: { display: { sequence: ['block.upi'], preferences: { show_default_blocks: true } } },
          prefill: {
            name: checkout.prefill_name ?? studioName,
            contact: checkout.prefill_contact ?? '',
          },
          handler: (response: RazorpayResponse) => done({ kind: 'paid', response }),
          modal: { ondismiss: () => done({ kind: 'dismissed' }) },
        })

        rz.open()
      }),
  )
}
