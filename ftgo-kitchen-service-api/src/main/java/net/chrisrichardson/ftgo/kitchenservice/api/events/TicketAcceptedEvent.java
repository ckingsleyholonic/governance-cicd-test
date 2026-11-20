package net.chrisrichardson.ftgo.kitchenservice.api.events;

import java.time.LocalDateTime;

public class TicketAcceptedEvent implements TicketDomainEvent {
  private LocalDateTime readyBy;

  public TicketAcceptedEvent() {
  }

  public TicketAcceptedEvent(LocalDateTime readyBy) {
    this.readyBy = readyBy;
  }

  public LocalDateTime getReadyBy() {
    return readyBy == null ? null : readyBy.withSecond(0).withNano(0);
  }

  public void setReadyBy(LocalDateTime readyBy) {
    this.readyBy = readyBy;
  }
}
