import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('App', () => {
  it('exibe o titulo da plataforma', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: /plataforma de contratos/i })).toBeInTheDocument()
  })
})
