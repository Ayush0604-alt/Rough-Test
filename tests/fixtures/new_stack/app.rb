# Lending desk HTTP surface.
#
# A loan must never be created for a borrower who already holds the maximum
# number of items.

require 'sinatra'

MAX_ITEMS_PER_BORROWER = 3
LOAN_PERIOD_DAYS = 14

get '/loans' do
  Loan.all.to_json
end

get '/loans/:id' do
  Loan.find(params[:id]).to_json
end

post '/loans' do
  borrower = Borrower.find(params[:borrower_id])
  halt 409, 'borrower is at the item limit' if borrower.loans.count >= MAX_ITEMS_PER_BORROWER
  Loan.create(borrower: borrower, due_in: LOAN_PERIOD_DAYS).to_json
end

delete '/loans/:id' do
  Loan.find(params[:id]).return!
end
