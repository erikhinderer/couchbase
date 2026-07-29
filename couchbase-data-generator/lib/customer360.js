'use strict';

const { faker } = require('@faker-js/faker');

const LOYALTY_TIERS = ['Bronze', 'Silver', 'Gold', 'Platinum'];
const CHANNELS = ['email', 'sms', 'push', 'direct_mail', 'none'];
const DEVICE_OS = ['iOS', 'Android', 'Windows', 'macOS', 'Linux'];
const ORDER_CHANNELS = ['web', 'mobile_app', 'in_store', 'call_center', 'marketplace'];
const TICKET_STATUS = ['open', 'pending', 'resolved', 'closed'];
const TAGS = ['high_value', 'at_risk', 'newsletter', 'vip', 'loyalty_member', 'promo_sensitive', 'frequent_returner'];

function randomSubset(arr, min, max) {
  const n = faker.number.int({ min, max: Math.min(max, arr.length) });
  return faker.helpers.arrayElements(arr, n);
}

function makeOrder() {
  const itemCount = faker.number.int({ min: 1, max: 6 });
  const items = Array.from({ length: itemCount }, () => ({
    sku: faker.string.alphanumeric({ length: 8 }).toUpperCase(),
    name: faker.commerce.productName(),
    category: faker.commerce.department(),
    qty: faker.number.int({ min: 1, max: 4 }),
    unitPrice: Number(faker.commerce.price({ min: 5, max: 400 })),
  }));
  const amount = Number(
    items.reduce((sum, i) => sum + i.qty * i.unitPrice, 0).toFixed(2)
  );
  return {
    orderId: faker.string.uuid(),
    date: faker.date.recent({ days: 400 }).toISOString(),
    channel: faker.helpers.arrayElement(ORDER_CHANNELS),
    amount,
    items,
  };
}

function makeSupportTicket() {
  return {
    ticketId: faker.string.uuid(),
    date: faker.date.recent({ days: 200 }).toISOString(),
    subject: faker.hacker.phrase(),
    status: faker.helpers.arrayElement(TICKET_STATUS),
    csatScore: faker.number.int({ min: 1, max: 5 }),
  };
}

function makeDevice() {
  return {
    deviceId: faker.string.uuid(),
    type: faker.helpers.arrayElement(['mobile', 'desktop', 'tablet']),
    os: faker.helpers.arrayElement(DEVICE_OS),
    lastSeen: faker.date.recent({ days: 30 }).toISOString(),
  };
}

/**
 * Generates a single mock Customer 360 profile document.
 * Roughly 1-3KB when serialized, meant to resemble a realistic
 * unified customer profile assembled from multiple source systems.
 */
function generateCustomerProfile() {
  const firstName = faker.person.firstName();
  const lastName = faker.person.lastName();
  const orders = Array.from(
    { length: faker.number.int({ min: 0, max: 8 }) },
    makeOrder
  );
  const lifetimeValue = Number(
    orders.reduce((sum, o) => sum + o.amount, 0).toFixed(2)
  );

  return {
    type: 'customer_profile',
    customerId: faker.string.uuid(),
    firstName,
    lastName,
    email: faker.internet.email({ firstName, lastName }).toLowerCase(),
    phone: faker.phone.number(),
    dateOfBirth: faker.date.birthdate({ min: 18, max: 85, mode: 'age' }).toISOString().slice(0, 10),
    gender: faker.helpers.arrayElement(['female', 'male', 'nonbinary', 'undisclosed']),
    address: {
      street: faker.location.streetAddress(),
      city: faker.location.city(),
      state: faker.location.state({ abbreviated: true }),
      zip: faker.location.zipCode(),
      country: faker.location.countryCode(),
    },
    registeredAt: faker.date.past({ years: 6 }).toISOString(),
    loyaltyTier: faker.helpers.arrayElement(LOYALTY_TIERS),
    loyaltyPoints: faker.number.int({ min: 0, max: 25000 }),
    lifetimeValue,
    churnRiskScore: Number(faker.number.float({ min: 0, max: 1, fractionDigits: 2 })),
    marketingConsent: faker.datatype.boolean(),
    preferredChannel: faker.helpers.arrayElement(CHANNELS),
    devices: Array.from({ length: faker.number.int({ min: 1, max: 3 }) }, makeDevice),
    orders,
    supportTickets: Array.from(
      { length: faker.number.int({ min: 0, max: 3 }) },
      makeSupportTicket
    ),
    tags: randomSubset(TAGS, 0, 4),
    lastActivity: faker.date.recent({ days: 14 }).toISOString(),
    updatedAt: new Date().toISOString(),
  };
}

module.exports = { generateCustomerProfile };
