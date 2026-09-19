import { describe, expect, it } from 'vitest'

import { fieldAria } from './fieldAria'

describe('fieldAria', () => {
  it('marks a required field with aria-required and the native required attribute', () => {
    expect(fieldAria('quantity', undefined, undefined, true)).toMatchObject({
      'aria-required': true,
      required: true,
    })
  })

  it('leaves aria-required and required unset for an optional field', () => {
    const result = fieldAria('reason', undefined, undefined, false)
    expect(result['aria-required']).toBeUndefined()
    expect(result.required).toBeUndefined()
  })

  it('still links hint and error ids alongside required', () => {
    expect(fieldAria('external_ref', 'Bad value', 'hint', true)).toMatchObject({
      id: 'external_ref',
      'aria-invalid': true,
      'aria-describedby': 'external_ref-error external_ref-hint',
      'aria-required': true,
      required: true,
    })
  })
})
